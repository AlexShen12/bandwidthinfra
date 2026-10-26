"""Model analysis and evidence validation, independent of GitHub writes."""

import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from pydantic import ValidationError

from reviewer.infra.bedrock import Completion, ModelError
from reviewer.infra.config import Limits
from reviewer.infra.models import InfraReviewResult
from reviewer.infra.safety import redact
from reviewer.infra.workspace import PreparedContext


class InvalidModelOutput(ModelError):
    pass


@dataclass
class ReviewOutcome:
    result: InfraReviewResult
    completions: list[Completion]
    dropped: list[str] = field(default_factory=list)

    @property
    def simulated(self):
        return any(item.simulated for item in self.completions)

    @property
    def stats(self):
        totals = {}
        for completion in self.completions:
            for key, value in completion.usage.items():
                if isinstance(value, (int, float)):
                    totals[key] = totals.get(key, 0) + value
        return {
            "model": self.completions[0].model,
            "seconds": round(sum(item.seconds for item in self.completions), 3),
            "usage": totals,
            "calls": len(self.completions),
            "simulated": self.simulated,
        }


def validate_evidence(result, context):
    if result.reviewed_head_sha != context.head_sha:
        raise ValueError("The response head SHA does not match the pinned review.")
    changed_paths = {item.file for item in context.diffs}
    prices = {record.id: record for record in context.pricing.records} if context.pricing else {}
    for finding in result.findings:
        if finding.file not in changed_paths:
            raise ValueError("Finding file is not in the supplied diff.")
        changed_reference = False
        for evidence in finding.evidence:
            if not context.supplied(
                evidence.snapshot, evidence.file, evidence.start_line, evidence.end_line
            ):
                raise ValueError("An evidence range was not supplied to the model.")
            # Changed attribution must concern this finding's file, not a random
            # second changed file used to justify an unrelated historical issue.
            matching = [diff for diff in context.diffs if diff.file == finding.file]
            allowed_paths = {
                diff.file if evidence.snapshot == "head" else diff.old_file for diff in matching
            }
            if evidence.file in allowed_paths and context.changed(
                evidence.snapshot, evidence.file, evidence.start_line, evidence.end_line
            ):
                changed_reference = True
        if not changed_reference:
            raise ValueError("Finding must cite a changed block in its own file.")
        for evidence_id in finding.cost_evidence_ids:
            record = prices.get(evidence_id)
            if (
                not record
                or not record.usable
                or record.base_sha != context.base_sha
                or record.head_sha != context.head_sha
            ):
                raise ValueError(
                    "Pricing evidence is unavailable or belongs to a different comparison."
                )
            if record.file != finding.file:
                raise ValueError("Pricing evidence does not match the finding file.")
        if finding.impact_basis == "workload_supported":
            raise ValueError(
                "No trusted workload profile was supplied; do not claim workload-supported impact."
            )


class InfrastructureReviewer:
    name = "infra"
    version = "0.1.0"

    def __init__(self, model_client, prompt, limits=None):
        self.client, self.prompt, self.limits = model_client, prompt, limits or Limits()
        self.last_outcome = None

    def run(self, context: PreparedContext) -> InfraReviewResult:
        user = json.dumps(context.model_input(), ensure_ascii=False)
        completions, errors = [], []
        for attempt in range(2):
            request = user
            if attempt:
                # Only validation diagnostics are fed back. Avoid unbounded raw
                # output and any secret the model might have repeated.
                data = context.model_input()
                data["validation_repair"] = {
                    "errors": errors,
                    "instruction": "Regenerate the complete JSON result from the original context. Correct all validation errors; do not add unsupported evidence.",
                }
                request = json.dumps(data, ensure_ascii=False)
            completion = self.client.complete(self.prompt, request, self.limits.max_output_tokens)
            completions.append(completion)
            try:
                if completion.stop_reason not in {"end_turn", "stop_sequence"}:
                    raise ValueError("Model output was truncated or did not finish normally.")
                result = InfraReviewResult.model_validate_json(completion.text)
                validate_evidence(result, context)
                break
            except (ValidationError, ValueError) as exc:
                if isinstance(exc, ValidationError):
                    errors = [
                        f"{'.'.join(map(str, item['loc']))}: {item['type']}"
                        for item in exc.errors(include_input=False, include_url=False)[:12]
                    ]
                else:
                    errors = [redact(str(exc))[:600]]
        else:
            raise InvalidModelOutput(
                "The model did not produce a valid evidence-backed result after one repair. No review was posted."
            )

        kept, dropped = [], []
        for finding in sorted(
            result.findings, key=lambda item: (item.severity != "high", -item.confidence)
        ):
            if finding.confidence < context.policy.min_confidence:
                dropped.append("Below configured confidence threshold")
                continue
            valid_lines = next(
                item.head_lines for item in context.diffs if item.file == finding.file
            )
            if finding.line not in valid_lines:
                finding = finding.model_copy(update={"line": None})
            duplicate = any(
                old.file == finding.file
                and old.rule_id == finding.rule_id
                and (
                    old.resource_operation.casefold() == finding.resource_operation.casefold()
                    or SequenceMatcher(None, old.title.casefold(), finding.title.casefold()).ratio()
                    > 0.8
                )
                for old in kept
            )
            if duplicate:
                dropped.append("Duplicate root cause")
                continue
            if len(kept) >= context.policy.max_comments:
                dropped.append("Configured comment cap")
                continue
            kept.append(finding)
        result = result.model_copy(update={"findings": kept})
        if context.omissions:
            # An input budget gap must not disappear just because the model
            # claimed complete coverage. Conservatively disclose it for all
            # reviewed categories; more precise category routing can come later.
            for entry in result.coverage:
                if entry.status == "reviewed":
                    entry.status = "limited"
                    entry.limitations = list(
                        dict.fromkeys(
                            [
                                *entry.limitations,
                                "Context collection omitted files/ranges; see the run's omissions.",
                            ]
                        )
                    )
        self.last_outcome = ReviewOutcome(result, completions, dropped)
        return result
