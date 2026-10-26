"""Application orchestration, authorization, deduplication, and posting."""

import contextlib
import json
import uuid
from pathlib import Path

from reviewer.core.commands import parse_command
from reviewer.infra import VERSION
from reviewer.infra.config import Limits, load_policy
from reviewer.infra.github import GitHubError, UncertainPost
from reviewer.infra.models import PricingInput
from reviewer.infra.render import marker, render_review
from reviewer.infra.reviewer import InfrastructureReviewer
from reviewer.infra.safety import display, redact
from reviewer.infra.workspace import GitSnapshots, collect_context

HELP = "Comment `@bandwidth-infra-reviewer review` to inspect nine infrastructure cost drivers, `review --full` to rerun this commit, or `help`. Reviews need write access. Dollar estimates require trusted pricing evidence."


def event_request(event, github, trigger):
    if event.get("action") != "created" or not event.get("issue", {}).get("pull_request"):
        return None
    comment = event.get("comment", {})
    user = comment.get("user", {})
    if user.get("type") == "Bot" or event.get("sender", {}).get("type") == "Bot":
        return None
    body = comment.get("body", "")
    command = parse_command(body, trigger)
    first = next((line.split() for line in body.splitlines() if line.strip()), [])
    if not command or len(first) < 2:
        return None
    if (
        command.name not in {"review", "help"}
        or (command.name == "help" and command.args)
        or any(arg != "--full" for arg in command.args)
        or len(command.args) > 1
    ):
        return None
    repo, pr = event["repository"]["full_name"], event["issue"]["number"]
    github.validate_repo(repo)
    if github.permission(repo, user.get("login", "")) not in {"admin", "maintain", "write"}:
        return None
    return {
        "repo": repo,
        "pr": pr,
        "actor": user["login"],
        "comment_id": comment["id"],
        "command": command.name,
        "full": "--full" in command.args,
    }


def load_pricing(path, base_sha, head_sha, changed_files):
    if path is None:
        return None
    path = Path(path)
    if path.stat().st_size > 200_000:
        raise ValueError("Pricing input is too large.")
    pricing = PricingInput.model_validate_json(path.read_text())
    for record in pricing.records:
        if record.base_sha != base_sha or record.head_sha != head_sha:
            raise ValueError("Pricing comparison SHAs do not match this review.")
        if record.file not in changed_files:
            raise ValueError("Pricing record file is not in the reviewed diff.")
    return pricing


def _publish(github, repo, number, head_sha, body, comments, run_marker, bot):
    try:
        return github.post_review(repo, number, head_sha, body, comments)
    except UncertainPost:
        existing = github.find_review(repo, number, run_marker, bot)
        if existing:
            return existing
        raise UncertainPost(
            "GitHub has not confirmed whether the review posted. Rerun the command; its marker will be checked before any further post."
        ) from None
    except GitHubError as exc:
        if not exc.line_error or not comments:
            raise
        fallback = (
            body
            + "\n\nInline placement was rejected; findings are retained here:\n\n"
            + "\n\n".join(
                f"`{display(comment['path'], 500)}:{comment['line']}`\n{comment['body']}"
                for comment in comments
            )
        )
        try:
            return github.post_review(repo, number, head_sha, fallback[:64_000], [])
        except UncertainPost:
            existing = github.find_review(repo, number, run_marker, bot)
            if existing:
                return existing
            raise


def run_review(
    github,
    *,
    repo,
    pr,
    model_factory,
    prompt_path,
    post=False,
    full=False,
    actor=None,
    comment_id=None,
    force_id=None,
    pricing_path=None,
    artifact_dir="artifacts",
    workspace_factory=GitSnapshots,
    trusted_ref=None,
):
    github.validate_repo(repo)
    if not isinstance(pr, int) or pr <= 0:
        raise ValueError("PR number must be positive.")
    if actor and github.permission(repo, actor) not in {"admin", "maintain", "write"}:
        return {"status": "unauthorized"}
    if post and not actor:
        raise ValueError(
            "Posting requires a verified event actor or an explicit --actor with repository write access."
        )
    pull = github.pull_request(repo, pr)
    ref = trusted_ref or github.trusted_ref(repo)
    policy, warnings = load_policy(github.text_file(repo, ".github/bandwidth-reviewer.yml", ref))
    labels = {label["name"] for label in pull.get("labels", [])}
    if pull.get("state") != "open" or policy.skip_label in labels or not policy.enabled:
        if post:
            github.reply(
                repo,
                pr,
                "Infrastructure review skipped: the PR is closed, the skip label is present, or the trusted configuration disables this reviewer.",
            )
        return {"status": "skipped"}
    head_sha, target_base = pull["head"]["sha"], pull["base"]["sha"]
    run_id = uuid.uuid4().hex
    run_marker = marker(
        head_sha, VERSION, (str(force_id or comment_id or run_id)) if full else None
    )
    bot = github.app_identity(repo) if post else None
    if post:
        existing = github.find_review(
            repo, pr, run_marker if full else marker(head_sha, VERSION), bot, any_run=not full
        )
        if existing:
            if comment_id:
                github.reply(
                    repo,
                    pr,
                    f"Already reviewed `{head_sha[:12]}`: {existing.get('html_url', '')}. Use `review --full` for an intentional rerun.",
                )
            return {"status": "already_reviewed", "review_url": existing.get("html_url")}

    check_id, reaction_id, record, succeeded = (
        None,
        None,
        {"run_id": run_id, "repo": repo, "pr": pr, "head_sha": head_sha},
        False,
    )
    try:
        if post:
            check_id = github.start_check(repo, head_sha, run_id)
            if comment_id:
                reaction_id = github.react(repo, comment_id, "eyes")["id"]
        comparison = github.compare(repo, target_base, head_sha)
        base_sha = comparison["merge_base_commit"]["sha"]
        with workspace_factory(base_sha, head_sha, repo=repo, token=github.token) as snapshots:
            context = collect_context(
                snapshots,
                repo=repo,
                pr=pr,
                target_base_sha=target_base,
                title=pull.get("title", ""),
                description=pull.get("body") or "",
                policy=policy,
            )
        context.omissions.extend(warnings)
        context.pricing = load_pricing(
            pricing_path, base_sha, head_sha, {item.file for item in context.diffs}
        )
        if not context.diffs:
            if post:
                github.finish_check(
                    repo,
                    check_id,
                    "neutral",
                    "No reviewable text changes",
                    "Nothing fit the supported text/budget rules. Check ignored paths or split the PR; no model call was made.",
                )
            record.update(status="no_context", omissions=context.omissions)
            return record
        client = model_factory()
        if post and getattr(client, "simulated", True):
            raise ValueError(
                "Simulated results cannot be published. Configure BEDROCK=real before posting."
            )
        reviewer = InfrastructureReviewer(client, Path(prompt_path).read_text(), Limits())
        reviewer.run(context)
        outcome = reviewer.last_outcome
        if post and outcome.simulated:
            raise ValueError(
                "Simulated completions cannot be published, regardless of the client type."
            )
        body, comments = render_review(context, outcome, run_marker)
        record.update(
            status="dry_run",
            result=outcome.result.model_dump(mode="json"),
            stats=outcome.stats,
            omissions=context.omissions,
            dropped=outcome.dropped,
            summary=body,
            comments=comments,
            base_sha=base_sha,
        )
        if not post:
            return record
        if github.pull_request(repo, pr)["head"]["sha"] != head_sha:
            github.finish_check(
                repo,
                check_id,
                "neutral",
                "New commits arrived",
                f"Reviewed `{head_sha[:12]}` was superseded. Nothing was posted; request another review on the latest commit.",
            )
            record["status"] = "superseded"
            return record
        # Covers a prior attempt that completed after the initial marker query.
        review = github.find_review(repo, pr, run_marker, bot)
        if not review:
            review = _publish(github, repo, pr, head_sha, body, comments, run_marker, bot)
        record.update(status="posted", review_url=review.get("html_url"))
        succeeded = True
        # Once the review committed, progress/reaction problems must not cause a
        # duplicate review or convert a published result to an analysis failure.
        with contextlib.suppress(Exception):
            github.finish_check(
                repo,
                check_id,
                "neutral" if outcome.result.findings else "success",
                f"{len(outcome.result.findings)} infrastructure cost concern(s)",
                body,
            )
        return record
    except Exception as exc:
        if post and check_id:
            with contextlib.suppress(Exception):
                github.finish_check(
                    repo,
                    check_id,
                    "neutral",
                    "Infrastructure review did not complete",
                    f"{display(str(exc), 2000)}\n\nRun `{run_id}`. Fix the indicated setup issue or retry the command. No successful analysis is being claimed.",
                )
        record.update(status="error", error=display(str(exc), 2000), error_type=type(exc).__name__)
        return record
    finally:
        if post and comment_id:
            if reaction_id:
                with contextlib.suppress(Exception):
                    github.remove_reaction(repo, comment_id, reaction_id)
            with contextlib.suppress(Exception):
                github.react(repo, comment_id, "rocket" if succeeded else "confused")
        # Final structured evidence is sufficient for adjudication; do not persist
        # full repository snapshots, raw model scratchpad, or credential material.
        try:
            directory = Path(artifact_dir)
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f"run-{run_id}.json"
            destination.write_text(redact(json.dumps(record, indent=2, ensure_ascii=False)))
        except OSError:
            record["artifact_warning"] = (
                "Could not save the local run artifact; the publication status is unchanged."
            )
