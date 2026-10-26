import json
from unittest.mock import Mock

import boto3
import pytest
import requests
from botocore.exceptions import ClientError
from conftest import result_dict
from pydantic import ValidationError

from reviewer.infra.bedrock import BedrockClient, ModelError
from reviewer.infra.config import Settings, load_policy
from reviewer.infra.github import GitHub, UncertainPost
from reviewer.infra.models import Coverage, PricingInput
from reviewer.infra.pipeline import load_pricing
from reviewer.infra.render import marker, render_review
from reviewer.infra.reviewer import InfrastructureReviewer


@pytest.mark.parametrize(
    ("code", "message", "expected"),
    [
        ("ExpiredTokenException", "expired", "expired"),
        ("AccessDeniedException", "not allowed", "denied model access"),
        ("ThrottlingException", "slow down", "throttled"),
        ("ValidationException", "bad model", "model/request"),
    ],
)
def test_bedrock_errors_distinguished(code, message, expected):
    client = Mock()
    client.converse.side_effect = ClientError(
        {"Error": {"Code": code, "Message": message}}, "Converse"
    )
    with pytest.raises(ModelError, match=expected):
        BedrockClient(Settings(), client).complete("system", "user", 8000)


def test_missing_api_key_does_not_fall_back_to_iam(monkeypatch):
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    with pytest.raises(ModelError, match="No Bedrock API key"):
        BedrockClient(Settings(mode="real", auth_mode="api-key"))


def test_iam_explicitly_excludes_bearer(monkeypatch):
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "stale-key")
    factory = Mock()
    monkeypatch.setattr("reviewer.infra.bedrock.boto3.client", factory)
    BedrockClient(Settings(mode="real", auth_mode="iam"))
    import os

    assert "AWS_BEARER_TOKEN_BEDROCK" not in os.environ
    assert factory.call_args.kwargs["config"].signature_version == "v4"


def test_api_key_uses_bearer_auth(monkeypatch):
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "fresh-key")
    factory = Mock()
    monkeypatch.setattr("reviewer.infra.bedrock.boto3.client", factory)
    BedrockClient(Settings(mode="real", auth_mode="api-key"))
    assert factory.call_args.kwargs["config"].signature_version == "bearer"


@pytest.mark.parametrize("mode", ["api-key", "iam"])
def test_sdk_prepares_selected_auth_without_network(monkeypatch, mode):
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "synthetic-bedrock-token")
    session = boto3.Session(
        aws_access_key_id="AKIASYNTHETICTESTONLY",
        aws_secret_access_key="synthetic-secret-test-only",
        aws_session_token="synthetic-session-test-only",
    )
    monkeypatch.setattr("reviewer.infra.bedrock.boto3.client", session.client)
    adapter = BedrockClient(Settings(mode="real", auth_mode=mode))
    observed = {}

    class Prepared(Exception):
        pass

    def intercept(request, **_):
        observed.update(dict(request.headers))
        raise Prepared()

    adapter.client.meta.events.register("before-send.bedrock-runtime.Converse", intercept)
    with pytest.raises(Prepared):
        adapter.complete("system", "user", 20)
    authorization = observed["Authorization"].decode()
    if mode == "api-key":
        assert authorization == "Bearer synthetic-bedrock-token"
        assert "X-Amz-Security-Token" not in observed
    else:
        assert authorization.startswith("AWS4-HMAC-SHA256 ")
        assert observed["X-Amz-Security-Token"] == b"synthetic-session-test-only"


def http_response(status, data):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(data).encode()
    return response


def test_installation_token_scope_is_checked():
    session = Mock()
    session.request.return_value = http_response(200, {"repositories": [{"full_name": "o/r"}]})
    github = GitHub("token", session, app_slug="infra-dev")
    assert github.app_identity("o/r") == "infra-dev[bot]"
    assert session.request.call_args.args[1].endswith("/installation/repositories")


def test_uncertain_post_is_not_blindly_retried():
    session = Mock()
    session.request.side_effect = requests.Timeout()
    with pytest.raises(UncertainPost):
        GitHub("token", session).post_review("o/r", 1, "a" * 40, "body", [])
    assert session.request.call_count == 1


def test_forced_marker_suppresses_later_normal_review():
    session = Mock()
    body = marker("a" * 40, "0.1.0", "request-1")
    session.request.return_value = http_response(
        200, [{"body": body, "state": "COMMENTED", "user": {"login": "infra[bot]"}}]
    )
    assert GitHub("token", session).find_review(
        "o/r", 1, marker("a" * 40, "0.1.0"), "infra[bot]", any_run=True
    )


def pricing_dict(context, **updates):
    record = {
        "id": "trusted-cost-1",
        "file": "app/service.py",
        "resource": "example_resource",
        "base_sha": context.base_sha,
        "head_sha": context.head_sha,
        "currency": "USD",
        "monthly_base": "100.00",
        "monthly_head": "250.00",
        "region": "us-west-2",
        "assumptions": ["Same fixed workload and region"],
        "available": True,
        "comparable": True,
        "source": "operator-supplied estimate",
        "source_version": "1",
        "generated_at": "2026-10-25T00:00:00Z",
        "unsupported_resources": [],
    }
    record.update(updates)
    return {"schema_version": "infra-pricing/v1", "records": [record]}


def test_pricing_rejects_mismatched_snapshot(context, tmp_path):
    path = tmp_path / "price.json"
    path.write_text(json.dumps(pricing_dict(context, head_sha="f" * 40)))
    with pytest.raises(ValueError, match="SHAs"):
        load_pricing(path, context.base_sha, context.head_sha, {"app/service.py"})


def test_pricing_unavailable_is_not_zero_and_currency_is_deterministic(context):
    from reviewer.infra.bedrock import FakeModelClient

    reviewer = InfrastructureReviewer(FakeModelClient([json.dumps(result_dict(context))]), "prompt")
    reviewer.run(context)
    context.pricing = PricingInput.model_validate_json(json.dumps(pricing_dict(context)))
    body, _ = render_review(context, reviewer.last_outcome, marker(context.head_sha, "0.1.0"))
    assert "$100.00" in body and "$250.00" in body and "threshold" in body
    context.pricing = PricingInput.model_validate_json(
        json.dumps(pricing_dict(context, available=False, monthly_base=None, monthly_head=None))
    )
    body, _ = render_review(context, reviewer.last_outcome, marker(context.head_sha, "0.1.0"))
    assert "$0.00" not in body
    assert "estimate unavailable" in body


@pytest.mark.parametrize(
    "changes",
    [
        {"monthly_base": "NaN"},
        {"monthly_head": "Infinity"},
        {"monthly_head": None},
        {"generated_at": "2026-10-25T00:00:00"},
    ],
)
def test_bad_pricing_is_rejected(context, changes):
    with pytest.raises(ValidationError):
        PricingInput.model_validate_json(json.dumps(pricing_dict(context, **changes)))


def test_coverage_cannot_smuggle_model_generated_prices():
    with pytest.raises(ValidationError, match="money amounts"):
        Coverage(category="requests", status="limited", limitations=["Could cost $200 monthly"])


def test_large_summary_keeps_commit_footer_and_marker(context):
    from reviewer.infra.bedrock import FakeModelClient

    reviewer = InfrastructureReviewer(FakeModelClient([json.dumps(result_dict(context))]), "prompt")
    reviewer.run(context)
    for entry in reviewer.last_outcome.result.coverage:
        entry.limitations = ["Long supported context limitation " * 20] * 50
    run_marker = marker(context.head_sha, "0.1.0")
    body, _ = render_review(context, reviewer.last_outcome, run_marker)
    assert len(body) < 60_000
    assert "Summary truncated" in body
    assert f"Reviewed `{context.head_sha}`" in body
    assert body.endswith(run_marker)


def test_policy_is_trusted_optional_and_safe():
    policy, warnings = load_policy(
        "version: 1\nreviewers:\n  infra:\n    min_confidence: 0.95\nmax_comments: 4\n"
    )
    assert policy.max_comments == 4 and policy.min_confidence == 0.95
    assert not warnings
    policy, warnings = load_policy("!!python/object:danger {}")
    assert policy.min_confidence == 0.85 and warnings
