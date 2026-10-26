import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from test_infra_pipeline import Gateway, event

from reviewer.infra.cli import main


def test_workflow_permissions_pins_default_branch_and_auth_order():
    data = yaml.load(Path(".github/workflows/infra-review.yml").read_text(), Loader=yaml.BaseLoader)
    assert "issue_comment" in data["on"]
    job = data["jobs"]["review"]
    assert job["timeout-minutes"] == "15"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    steps = job["steps"]
    for step in steps:
        if "uses" in step:
            assert len(step["uses"].split("@")[1]) == 40
    checkout = steps[0]
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert checkout["with"]["persist-credentials"] == "false"
    app = next(step for step in steps if step.get("id") == "app")
    assert app["with"]["repositories"] == "${{ github.event.repository.name }}"
    for permission in ("permission-issues", "permission-checks", "permission-pull-requests"):
        assert app["with"][permission] == "write"
    ids = [step.get("id") for step in steps]
    assert ids.index("authorize") < ids.index("aws")
    publish = steps[-1]
    assert "== 'api-key'" in publish["env"]["AWS_BEARER_TOKEN_BEDROCK"]
    assert publish["env"]["BEDROCK"] == "real"


def test_authorize_only_never_initializes_bedrock(context, tmp_path, monkeypatch):
    path, output = tmp_path / "event.json", tmp_path / "output"
    path.write_text(json.dumps(event()))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr("reviewer.infra.cli.GitHub", lambda *_, **__: Gateway(context))
    model = Mock(side_effect=AssertionError("Bedrock must not be initialized"))
    monkeypatch.setattr("reviewer.infra.cli.BedrockClient", model)
    assert main(["--event-path", str(path), "--authorize-only"]) == 0
    assert "authorized=true" in output.read_text()
    model.assert_not_called()


def test_app_manifest_webhooks_disabled_and_minimal_permissions():
    manifest = json.loads(Path(".github/infra-reviewer/app-manifest.json").read_text())
    assert manifest["hook_attributes"]["active"] is False
    assert manifest["default_events"] == []
    assert manifest["default_permissions"] == {
        "contents": "read",
        "metadata": "read",
        "pull_requests": "write",
        "issues": "write",
        "checks": "write",
    }


def test_oidc_custom_subject_includes_workflow_identity():
    template = json.loads(Path(".github/infra-reviewer/oidc-subject.json").read_text())
    assert template == {
        "use_default": False,
        "include_claim_keys": ["repo", "ref", "workflow_ref"],
    }
    variables = Path("infra/actions-oidc/variables.tf").read_text()
    assert ":workflow_ref:" in variables
    assert "branch-only and wildcard subjects are not accepted" in variables


def test_registration_never_passes_key_in_command_arguments(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("register_app", "scripts/register_app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    subprocess = Mock()
    monkeypatch.setattr(module.subprocess, "run", subprocess)
    pem = "-----BEGIN PRIVATE KEY-----\nfixture-key-only\n-----END PRIVATE KEY-----\n"
    metadata = module.save_and_configure(
        {"id": 123, "slug": "infra-fixture", "pem": pem}, "owner/repo", tmp_path / "secrets"
    )
    key = Path(metadata["key_path"])
    assert key.stat().st_mode & 0o777 == 0o600
    assert subprocess.call_args_list[0].kwargs["input"] == pem
    assert all(pem not in str(call.args) for call in subprocess.call_args_list)
    assert pem not in (tmp_path / "secrets/infra-app-registration.json").read_text()
    with pytest.raises(FileExistsError):
        module.save_and_configure(
            {"id": 123, "slug": "infra-fixture", "pem": pem}, "owner/repo", tmp_path / "secrets"
        )
