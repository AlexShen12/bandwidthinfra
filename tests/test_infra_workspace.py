import os

import pytest
from conftest import git, make_repo

from reviewer.infra.config import Limits, Policy
from reviewer.infra.safety import display, redact
from reviewer.infra.workspace import GitSnapshots, annotate_patch, collect_context, safe_path


def test_exact_diff_evidence_ranges(context):
    diff = context.diffs[0]
    assert diff.changed_head == {2}
    assert diff.changed_base == {2}
    assert context.supplied("head", "app/service.py", 1, 2)
    assert not context.supplied("head", "app/service.py", 2, 3)
    assert "BASE   HEAD" in diff.annotated


def test_multi_hunk_annotation_and_no_newline():
    patch = (
        "@@ -1,2 +1,2 @@\n old\n-gone\n+new\n@@ -20 +20 @@\n-a\n+b\n\\ No newline at end of file\n"
    )
    diff = annotate_patch("x", "x", "M", patch)
    assert diff.changed_head == {2, 20}
    assert diff.changed_base == {2, 20}
    assert diff.head_lines == {1, 2, 20}


def test_added_deleted_renamed_and_non_python(tmp_path):
    path = tmp_path / "repo"
    old = "function send(xs) {\n  return batchSend(xs);\n}\n" + "\n" * 10
    base, head = make_repo(
        path,
        {"old.ts": old, "deleted.sql": "SELECT id FROM orders;\n"},
        {
            "new.ts": old.replace("batchSend(xs)", "xs.map(sendOne)"),
            "added.tf": 'resource "x" "y" {}\n',
        },
    )
    with GitSnapshots(base, head, local_path=path) as source:
        ctx = collect_context(source, repo="o/r", pr=1, target_base_sha=base)
    assert {item.status for item in ctx.diffs} >= {"A", "D", "R"}
    assert next(item for item in ctx.diffs if item.file == "deleted.sql").head_lines == set()
    assert any(item.file == "new.ts" for item in ctx.diffs)


@pytest.mark.parametrize(
    "path", ["../secret", "/etc/passwd", ".git/config", "a/../../x", "a\\b", "a\x00b"]
)
def test_unsafe_paths_are_blocked(path):
    assert not safe_path(path)


def test_symlinks_binaries_and_secrets_never_become_context(tmp_path):
    path = tmp_path / "repo"
    base, _ = make_repo(path, {"keep.py": "x = 1\n"}, {"keep.py": "x = 1\n"})
    os.symlink("/etc/passwd", path / "leak.py")
    (path / "binary.py").write_bytes(b"binary\x00content")
    (path / ".env").write_text("PRIVATE_KEY=secret")
    git(path, "add", ".")
    git(path, "commit", "-qm", "unsafe inputs")
    head = git(path, "rev-parse", "HEAD")
    with GitSnapshots(base, head, local_path=path) as source:
        ctx = collect_context(source, repo="o/r", pr=1, target_base_sha=base)
    assert not ctx.diffs
    assert any("symlink" in item for item in ctx.omissions)
    assert any("binary" in item for item in ctx.omissions)
    assert all("PRIVATE_KEY=secret" not in str(value) for value in ctx.model_input().values())


def test_budget_omissions_are_explicit(context):
    with GitSnapshots(context.base_sha, context.head_sha, local_path=context.local_path) as source:
        ctx = collect_context(
            source,
            repo="o/r",
            pr=1,
            target_base_sha=context.base_sha,
            limits=Limits(diff_chars=1, context_chars=1),
        )
    assert not ctx.diffs
    assert any("budget" in item for item in ctx.omissions)


def test_regular_file_replaced_by_symlink_is_excluded(tmp_path):
    path = tmp_path / "repo"
    base, _ = make_repo(path, {"app.py": "x = 1\n"}, {"app.py": "x = 1\n"})
    (path / "app.py").unlink()
    os.symlink("/private/secret-target", path / "app.py")
    (path / "nested/.secrets").mkdir(parents=True)
    (path / "nested/.secrets/token.txt").write_text("hidden-secret-value")
    git(path, "add", ".")
    git(path, "commit", "-qm", "replace file")
    head = git(path, "rev-parse", "HEAD")
    with GitSnapshots(base, head, local_path=path) as source:
        ctx = collect_context(source, repo="o/r", pr=1, target_base_sha=base)
    assert not ctx.diffs
    assert not ctx.excerpts
    assert "secret-target" not in str(ctx.model_input())
    assert "hidden-secret-value" not in str(ctx.model_input())
    assert any("symlink" in item for item in ctx.omissions)


def test_related_wrapper_beyond_first_forty_lines_is_collected(tmp_path):
    path = tmp_path / "repo"
    helper = (
        "# supporting context\n" * 70
        + "def send_customer(client, customer):\n    return client.send(customer)\n"
    )
    base, head = make_repo(
        path,
        {
            "app.py": "from helpers import send_customer\ndef send(client, customers):\n    return client.send_batch(customers)\n",
            "helpers.py": helper,
        },
        {
            "app.py": "from helpers import send_customer\ndef send(client, customers):\n    return [send_customer(client, c) for c in customers]\n",
            "helpers.py": helper,
        },
    )
    with GitSnapshots(base, head, local_path=path) as source:
        ctx = collect_context(source, repo="o/r", pr=1, target_base_sha=base)
    assert any(
        "def send_customer" in item.content for item in ctx.excerpts if item.file == "helpers.py"
    )


def test_ignore_policy_applies_to_related_files(context):
    with GitSnapshots(context.base_sha, context.head_sha, local_path=context.local_path) as source:
        ctx = collect_context(
            source,
            repo="o/r",
            pr=1,
            target_base_sha=context.base_sha,
            policy=Policy(ignore_paths=["app/**"]),
        )
    assert not ctx.diffs


def test_fetch_auth_is_not_saved_and_diff_execution_disabled(tmp_path, monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = b""

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    monkeypatch.setattr("reviewer.infra.workspace.subprocess.run", run)
    workspace = GitSnapshots("a" * 40, "b" * 40, repo="o/r", token="never-print-me")
    with workspace:
        directory = workspace.path
        workspace.patch("a.py", "a.py")
        assert not (directory / ".git/config").exists()
    assert not directory.exists()
    fetches = [(command, opts) for command, opts in calls if "fetch" in command]
    assert all("never-print-me" not in str(command) for command, _ in fetches)
    assert all("GIT_CONFIG_VALUE_0" in opts["env"] for _, opts in fetches)
    assert any("--no-ext-diff" in command and "--no-textconv" in command for command, _ in calls)


def test_redaction_preserves_line_numbers_and_inert_output():
    text = 'API_TOKEN = "secret-not-for-the-model"\n-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n'
    masked = redact(text)
    assert masked.count("\n") == text.count("\n")
    assert "secret-not-for-the-model" not in masked
    assert "\nabc\n" not in masked
    assert "<script>" not in display(
        "<script>alert</script> @someone ![leak](https://example.test/x)"
    )
    assert "@someone" not in display("@someone")
