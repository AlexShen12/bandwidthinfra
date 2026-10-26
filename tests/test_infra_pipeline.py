import pytest

from reviewer.infra.bedrock import Completion, FakeModelClient, ModelError
from reviewer.infra.github import GitHubError, UncertainPost
from reviewer.infra.pipeline import event_request, run_review
from reviewer.infra.workspace import GitSnapshots


class Model:
    simulated = False

    def __init__(self, response):
        self.response = response
        self.calls = 0

    def complete(self, *_):
        self.calls += 1
        return Completion(self.response, "offline-test-double")


class Gateway:
    token = "test-token"

    def __init__(self, context):
        self.context = context
        self.access = "write"
        self.reviews = []
        self.posts = []
        self.checks = []
        self.replies = []
        self.pull_calls = 0
        self.supersede = False
        self.fail_after_post = False
        self.reject_line = False

    def validate_repo(self, repo):
        pass

    def permission(self, *_):
        return self.access

    def pull_request(self, *_):
        self.pull_calls += 1
        head = "f" * 40 if self.supersede and self.pull_calls > 1 else self.context.head_sha
        return {
            "head": {"sha": head},
            "base": {"sha": self.context.base_sha},
            "state": "open",
            "labels": [],
            "title": "Update delivery",
            "body": "",
        }

    def trusted_ref(self, *_):
        return "c" * 40

    def text_file(self, *_):
        return None

    def app_identity(self, *_):
        return "infra-dev[bot]"

    def find_review(self, repo, pr, marker, bot, any_run=False):
        return next(
            (
                review
                for review in self.reviews
                if (marker.removesuffix(" -->") if any_run else marker) in review["body"]
            ),
            None,
        )

    def compare(self, *_):
        return {"merge_base_commit": {"sha": self.context.base_sha}}

    def start_check(self, *_):
        return 1

    def finish_check(self, *args):
        self.checks.append(args)

    def react(self, *_):
        return {"id": 1}

    def remove_reaction(self, *_):
        pass

    def reply(self, *args):
        self.replies.append(args)

    def post_review(self, repo, pr, head, body, comments):
        self.posts.append((head, body, comments))
        if self.reject_line and comments:
            raise GitHubError(422, "Invalid line", line_error=True)
        review = {"body": body, "html_url": "https://github.com/o/r/pull/1#review"}
        self.reviews.append(review)
        if self.fail_after_post:
            raise UncertainPost("connection lost")
        return review


def run(context, response, prompt, tmp_path, gateway=None, **options):
    gateway = gateway or Gateway(context)
    model = Model(response)

    def workspace(base, head, **_):
        return GitSnapshots(base, head, local_path=context.local_path)

    output = run_review(
        gateway,
        repo="owner/repo",
        pr=1,
        actor="writer",
        model_factory=lambda: model,
        prompt_path=prompt,
        artifact_dir=tmp_path / "artifacts",
        workspace_factory=workspace,
        **options,
    )
    return output, gateway, model


def test_post_success_and_repeated_command_deduplicates(context, response, prompt, tmp_path):
    result, gateway, model = run(context, response, prompt, tmp_path, post=True)
    assert result["status"] == "posted"
    assert len(gateway.posts) == 1
    assert gateway.posts[0][0] == context.head_sha
    assert gateway.posts[0][2][0]["line"] == 2
    result, gateway, model = run(context, response, prompt, tmp_path, gateway, post=True)
    assert result["status"] == "already_reviewed"
    assert model.calls == 0
    assert len(gateway.posts) == 1


def test_full_intent_reruns_once_for_same_request(context, response, prompt, tmp_path):
    _, gateway, _ = run(context, response, prompt, tmp_path, post=True)
    result, _, _ = run(
        context, response, prompt, tmp_path, gateway, post=True, full=True, force_id="command-123"
    )
    assert result["status"] == "posted"
    result, _, model = run(
        context, response, prompt, tmp_path, gateway, post=True, full=True, force_id="command-123"
    )
    assert result["status"] == "already_reviewed"
    assert model.calls == 0
    assert len(gateway.posts) == 2


def test_crash_after_post_reconciles_before_retry(context, response, prompt, tmp_path):
    gateway = Gateway(context)
    gateway.fail_after_post = True
    result, _, _ = run(context, response, prompt, tmp_path, gateway, post=True)
    assert result["status"] == "posted"
    assert len(gateway.posts) == 1


def test_superseded_commit_has_no_post(context, response, prompt, tmp_path):
    gateway = Gateway(context)
    gateway.supersede = True
    result, _, _ = run(context, response, prompt, tmp_path, gateway, post=True)
    assert result["status"] == "superseded"
    assert not gateway.posts
    assert gateway.checks[-1][2] == "neutral"


def test_line_422_falls_back_to_summary(context, response, prompt, tmp_path):
    gateway = Gateway(context)
    gateway.reject_line = True
    result, _, _ = run(context, response, prompt, tmp_path, gateway, post=True)
    assert result["status"] == "posted"
    assert len(gateway.posts) == 2
    assert not gateway.posts[-1][2]
    assert "retained here" in gateway.posts[-1][1]


def test_dry_run_has_no_writes(context, response, prompt, tmp_path):
    result, gateway, _ = run(context, response, prompt, tmp_path)
    assert result["status"] == "dry_run"
    assert not gateway.posts and not gateway.checks
    assert list((tmp_path / "artifacts").glob("run-*.json"))


def test_unauthorized_never_calls_model(context, response, prompt, tmp_path):
    gateway = Gateway(context)
    gateway.access = "read"
    result, _, model = run(context, response, prompt, tmp_path, gateway, post=True)
    assert result["status"] == "unauthorized"
    assert model.calls == 0


def test_missing_credential_setup_is_neutral(context, prompt, tmp_path):
    gateway = Gateway(context)

    def factory():
        raise ModelError("Bedrock credentials expired")

    result = run_review(
        gateway,
        repo="owner/repo",
        pr=1,
        actor="writer",
        post=True,
        prompt_path=prompt,
        model_factory=factory,
        artifact_dir=tmp_path / "artifacts",
        workspace_factory=lambda base, head, **_: GitSnapshots(
            base, head, local_path=context.local_path
        ),
    )
    assert result["status"] == "error"
    assert not gateway.posts
    assert gateway.checks[-1][2] == "neutral"


def test_simulated_publication_is_impossible(context, prompt, tmp_path):
    gateway = Gateway(context)
    result = run_review(
        gateway,
        repo="owner/repo",
        pr=1,
        actor="writer",
        post=True,
        prompt_path=prompt,
        model_factory=FakeModelClient,
        artifact_dir=tmp_path / "artifacts",
        workspace_factory=lambda base, head, **_: GitSnapshots(
            base, head, local_path=context.local_path
        ),
    )
    assert result["status"] == "error"
    assert not gateway.posts


def event(body="@bandwidth-infra-reviewer review"):
    return {
        "action": "created",
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 1, "pull_request": {"url": "https://github.com/o/r/pull/1"}},
        "comment": {"id": 3, "body": body, "user": {"login": "writer", "type": "User"}},
        "sender": {"type": "User"},
    }


@pytest.mark.parametrize(
    "body",
    [
        "@bandwidth-infra-reviewer review",
        "\n\n@BANDWIDTH-INFRA-REVIEWER review --full",
        "@bandwidth-infra-reviewer help",
    ],
)
def test_exact_authorized_commands(context, body):
    assert event_request(event(body), Gateway(context), "@bandwidth-infra-reviewer")


@pytest.mark.parametrize(
    "body",
    [
        "@bandwidth-infra-reviewer",
        "first line\n@bandwidth-infra-reviewer review",
        "@bandwidth-infra-reviewer-extra review",
        "@bandwidth-infra-reviewer review --full --full",
        "@bandwidth-infra-reviewer review infra",
        "@bandwidth-infra-reviewer help extra",
    ],
)
def test_non_commands_do_not_review(context, body):
    assert event_request(event(body), Gateway(context), "@bandwidth-infra-reviewer") is None


def test_bot_and_non_writer_events_do_not_review(context):
    data, gateway = event(), Gateway(context)
    data["comment"]["user"]["type"] = "Bot"
    assert event_request(data, gateway, "@bandwidth-infra-reviewer") is None
    data["comment"]["user"]["type"] = "User"
    gateway.access = "read"
    assert event_request(data, gateway, "@bandwidth-infra-reviewer") is None
