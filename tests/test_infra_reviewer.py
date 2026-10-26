import copy
import json

import pytest
from conftest import finding_dict, result_dict
from pydantic import ValidationError

from reviewer.infra.bedrock import Completion, FakeModelClient
from reviewer.infra.config import Policy
from reviewer.infra.models import CATEGORIES, InfraFinding, InfraReviewResult
from reviewer.infra.reviewer import InfrastructureReviewer, InvalidModelOutput


def test_one_call_validated_result_and_common_adapter(context, response):
    client = FakeModelClient([response])
    reviewer = InfrastructureReviewer(client, "instructions")
    result = reviewer.run(context)
    assert len(client.calls) == 1
    assert result.findings[0].to_common().reviewer == "infra"
    assert result.findings[0].to_common().line == 2
    assert reviewer.last_outcome.simulated


@pytest.mark.parametrize("bad", ["not JSON", '{"findings":', "```json\n{}\n```"])
def test_one_repair_then_valid(context, response, bad):
    client = FakeModelClient([bad, response])
    reviewer = InfrastructureReviewer(client, "instructions")
    assert reviewer.run(context).findings
    assert len(client.calls) == 2
    assert "validation_repair" in json.loads(client.calls[1][1])


def test_repeated_bad_output_is_failure(context):
    client = FakeModelClient(["bad", "bad"])
    with pytest.raises(InvalidModelOutput):
        InfrastructureReviewer(client, "prompt").run(context)
    assert len(client.calls) == 2


def test_truncation_requires_repair(context, response):
    class Client:
        calls = 0

        def complete(self, *_):
            self.calls += 1
            return Completion(
                response, "test", stop_reason="max_tokens" if self.calls == 1 else "end_turn"
            )

    client = Client()
    InfrastructureReviewer(client, "prompt").run(context)
    assert client.calls == 2


@pytest.mark.parametrize("mutation", ["head", "path", "range", "unchanged", "pricing", "workload"])
def test_unsupported_claim_cannot_survive(context, response, mutation):
    data = json.loads(response)
    finding = data["findings"][0]
    if mutation == "head":
        data["reviewed_head_sha"] = "f" * 40
    elif mutation == "path":
        finding["file"] = "unrelated.py"
    elif mutation == "range":
        finding["evidence"][0]["end_line"] = 900
    elif mutation == "unchanged":
        finding["evidence"][0].update(start_line=1, end_line=1)
    elif mutation == "pricing":
        finding["cost_evidence_ids"] = ["made-up"]
    else:
        finding["impact_basis"] = "workload_supported"
    bad = json.dumps(data)
    with pytest.raises(InvalidModelOutput):
        InfrastructureReviewer(FakeModelClient([bad, bad]), "prompt").run(context)


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_invalid_confidence(confidence):
    with pytest.raises(ValidationError):
        InfraFinding.model_validate(finding_dict(confidence=confidence))


@pytest.mark.parametrize(
    "body",
    ["This costs $150 per month.", "An increase of 150 USD.", "USD 150 more.", "150 dollars more."],
)
def test_money_must_be_rendered_from_evidence(body):
    with pytest.raises(ValidationError):
        InfraFinding.model_validate(finding_dict(body=body))


@pytest.mark.parametrize("category", CATEGORIES)
def test_each_category_is_supported(context, category):
    data = result_dict(context, findings=[finding_dict(category)])
    assert InfraReviewResult.model_validate_json(json.dumps(data)).findings[0].category == category


def test_coverage_requires_exactly_each_category(context):
    data = result_dict(context)
    data["coverage"][0] = copy.deepcopy(data["coverage"][1])
    with pytest.raises(ValidationError):
        InfraReviewResult.model_validate_json(json.dumps(data))


def test_filter_duplicate_and_low_confidence_and_invalid_inline(context):
    context.policy = Policy(max_comments=1)
    findings = [
        finding_dict(line=900),
        finding_dict(title="Same call repeated"),
        finding_dict(rule_id="infra/compute-growth", category="compute", confidence=0.84),
    ]
    reviewer = InfrastructureReviewer(
        FakeModelClient([json.dumps(result_dict(context, findings=findings))]), "prompt"
    )
    result = reviewer.run(context)
    assert len(result.findings) == 1
    assert result.findings[0].line is None
    assert result.findings[0].to_common().line is None
    assert len(reviewer.last_outcome.dropped) == 2


def test_empty_valid_result_is_allowed(context):
    assert (
        not InfrastructureReviewer(FakeModelClient([json.dumps(result_dict(context))]), "prompt")
        .run(context)
        .findings
    )


def test_omissions_cannot_be_claimed_as_complete_coverage(context, response):
    context.omissions.append("helpers.py: context truncated")
    result = InfrastructureReviewer(FakeModelClient([response]), "prompt").run(context)
    assert all(item.status == "limited" for item in result.coverage)
