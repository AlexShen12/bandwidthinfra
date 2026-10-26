import json
from pathlib import Path

import pytest

from reviewer.infra.evaluate import fixture_context, score
from reviewer.infra.models import CATEGORIES

CASES = json.loads(Path("eval/cases.json").read_text())


def test_fixture_suite_has_positive_and_control_for_every_category():
    for category in CATEGORIES:
        cases = [case for case in CASES if case["category"] == category]
        assert len(cases) == 2
        assert sum(bool(case["expected"]) for case in cases) == 1
    assert any(any(path.endswith(".ts") for path in case["head_files"]) for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_fixtures_prepare_consistent_context_without_running_source(case):
    with fixture_context(case) as context:
        assert context.base_sha != context.head_sha
        assert context.diffs
        assert context.model_input()["review_metadata"]["head_sha"] == context.head_sha
        assert "expected" not in context.model_input()
        if case["id"] == "requests-negative":
            assert any("lru_cache" in item.content for item in context.excerpts)
        if case["id"] == "storage-negative":
            assert any("expiration" in item.content for item in context.excerpts)


def test_human_scoring_reports_raw_counts_and_misses():
    cases = [
        {
            "id": "case",
            "expected": [{"id": "x", "category": "requests"}, {"id": "y", "category": "compute"}],
        }
    ]
    runs = {
        "case": {
            "stats": {"simulated": False},
            "result": {"findings": [{"category": "requests"}, {"category": "requests"}]},
        }
    }
    report = score(
        cases,
        runs,
        [
            {"case_id": "case", "finding_index": 0, "expected_id": "x"},
            {"case_id": "case", "finding_index": 1, "expected_id": None},
        ],
    )
    assert report["requests"]["tp"] == 1
    assert report["requests"]["fp"] == 1
    assert report["requests"]["precision"] == 0.5
    assert report["compute"]["fn"] == 1


def test_fake_outputs_are_never_model_precision():
    with pytest.raises(ValueError, match="Simulated"):
        score(
            [{"id": "x", "expected": []}],
            {"x": {"stats": {"simulated": True}, "result": {"findings": []}}},
            [],
        )


def test_unjudged_findings_cannot_be_scored():
    with pytest.raises(ValueError, match="incomplete"):
        score(
            [{"id": "x", "expected": []}],
            {
                "x": {
                    "stats": {"simulated": False},
                    "result": {"findings": [{"category": "requests"}]},
                }
            },
            [],
        )
