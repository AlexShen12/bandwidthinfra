import json
import subprocess
from pathlib import Path

import pytest

from reviewer.infra.models import CATEGORIES, RULES
from reviewer.infra.workspace import GitSnapshots, collect_context


@pytest.fixture(autouse=True)
def forbid_live_http(monkeypatch):
    def blocked(*_, **__):
        raise AssertionError("Offline tests must not send HTTP requests")

    monkeypatch.setattr("requests.sessions.Session.request", blocked)
    monkeypatch.setattr("botocore.httpsession.URLLib3Session.send", blocked)


def git(path, *args):
    return (
        subprocess.check_output(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
            cwd=path,
            stderr=subprocess.PIPE,
        )
        .decode()
        .strip()
    )


def make_repo(path, base_files, head_files):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.name", "Offline fixture")
    git(path, "config", "user.email", "fixture@example.invalid")
    for filename, text in base_files.items():
        file = path / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
    git(path, "add", ".")
    git(path, "commit", "-qm", "base", "--allow-empty")
    base = git(path, "rev-parse", "HEAD")
    for filename in set(base_files) - set(head_files):
        (path / filename).unlink()
    for filename, text in head_files.items():
        file = path / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-qm", "head", "--allow-empty")
    return base, git(path, "rev-parse", "HEAD")


@pytest.fixture
def context(tmp_path):
    path = tmp_path / "repo"
    base, head = make_repo(
        path,
        {"app/service.py": "def run(items, store):\n    return store.batch_get(items)\n"},
        {
            "app/service.py": "def run(items, store):\n    return [store.get(item) for item in items]\n"
        },
    )
    with GitSnapshots(base, head, local_path=path) as source:
        ctx = collect_context(source, repo="owner/repo", pr=1, target_base_sha=base)
    ctx.local_path = path
    return ctx


def result_dict(context, *, findings=None):
    return {
        "schema_version": "infra-review/v1",
        "reviewed_head_sha": context.head_sha,
        "findings": findings or [],
        "coverage": [
            {"category": category, "status": "reviewed", "limitations": []}
            for category in CATEGORIES
        ],
    }


def finding_dict(category="requests", **updates):
    item = {
        "rule_id": RULES[category],
        "category": category,
        "file": "app/service.py",
        "line": 2,
        "severity": "medium",
        "confidence": 0.95,
        "title": "Batch lookup becomes a request per item",
        "body": "This change replaces the batch read with a remote request per recipient. Keep the batch read to avoid request amplification.",
        "resource_operation": "store.get",
        "consumption_drivers": ["requests"],
        "entry_point": "run",
        "call_path": ["run", "store.get"],
        "before_behavior": "One batch operation",
        "after_behavior": "One operation per item",
        "relative_consumption": "Up to N calls for N items",
        "impact_basis": "structural",
        "evidence": [
            {
                "snapshot": "head",
                "file": "app/service.py",
                "start_line": 2,
                "end_line": 2,
                "observation": "get is called for each item",
            }
        ],
        "mitigations_checked": ["No batch remains in the changed function"],
        "assumptions": ["Monthly usage is unknown"],
        "recommended_action": "Retain the batch lookup.",
        "verification_needed": [],
        "cost_evidence_ids": [],
    }
    item.update(updates)
    return item


@pytest.fixture
def response(context):
    return json.dumps(result_dict(context, findings=[finding_dict()]))


@pytest.fixture
def prompt():
    return Path(".github/infra-reviewer/prompt.md")
