"""Strict model output and operator-supplied pricing contracts."""

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reviewer.core.models import Finding

CATEGORIES = (
    "requests",
    "database",
    "compute",
    "jobs",
    "retries",
    "transfer",
    "storage",
    "telemetry",
    "capacity",
)
RULES = dict(
    zip(
        CATEGORIES,
        (
            "infra/request-amplification",
            "infra/database-work",
            "infra/compute-growth",
            "infra/job-amplification",
            "infra/retry-amplification",
            "infra/data-transfer",
            "infra/storage-growth",
            "infra/telemetry-growth",
            "infra/capacity-change",
        ),
        strict=True,
    )
)
Category = Literal[
    "requests",
    "database",
    "compute",
    "jobs",
    "retries",
    "transfer",
    "storage",
    "telemetry",
    "capacity",
]
SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Text = Annotated[str, Field(min_length=1, max_length=6000)]
MONEY = re.compile(
    r"[$€£]\s*[-+]?\d|\b(?:USD|EUR|GBP)\s*[-+]?\d|\b\d[\d,.]*\s*(?:USD|EUR|GBP|dollars|euros|pounds)\b",
    re.I,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Evidence(StrictModel):
    snapshot: Literal["base", "head"]
    file: Text
    start_line: int = Field(gt=0, le=10_000_000)
    end_line: int = Field(gt=0, le=10_000_000)
    observation: Text

    @model_validator(mode="after")
    def ordered(self):
        if self.end_line < self.start_line or self.end_line - self.start_line > 1000:
            raise ValueError("Evidence must be an ordered range of at most 1001 lines")
        return self


class InfraFinding(StrictModel):
    rule_id: Text
    category: Category
    file: Text
    line: int | None = Field(gt=0, le=10_000_000)
    severity: Literal["high", "medium"]
    confidence: float = Field(ge=0, le=1)
    title: Annotated[str, Field(min_length=1, max_length=250)]
    body: Text
    resource_operation: Text
    consumption_drivers: list[
        Literal[
            "requests",
            "database_work",
            "compute",
            "transfer",
            "storage",
            "telemetry",
            "provisioned_capacity",
        ]
    ] = Field(min_length=1, max_length=7)
    entry_point: Text
    call_path: list[Text] = Field(max_length=30)
    before_behavior: Text
    after_behavior: Text
    relative_consumption: Text | None
    impact_basis: Literal["structural", "workload_supported", "tool_estimated"]
    evidence: list[Evidence] = Field(min_length=1, max_length=30)
    mitigations_checked: list[Text] = Field(max_length=30)
    assumptions: list[Text] = Field(max_length=30)
    recommended_action: Text
    verification_needed: list[Text] = Field(max_length=30)
    cost_evidence_ids: list[Text] = Field(max_length=30)

    @model_validator(mode="after")
    def matching_rule(self):
        if RULES[self.category] != self.rule_id:
            raise ValueError("Category and rule_id do not match")
        displayed = (
            self.title,
            self.body,
            self.recommended_action,
            self.relative_consumption or "",
            *self.assumptions,
        )
        if any(MONEY.search(value) for value in displayed):
            raise ValueError("Model-generated money amounts are forbidden; reference pricing IDs")
        if self.impact_basis == "tool_estimated" and not self.cost_evidence_ids:
            raise ValueError("Tool-estimated findings require pricing evidence IDs")
        return self

    def to_common(self) -> Finding:
        return Finding(
            reviewer="infra",
            rule_id=self.rule_id,
            file=self.file,
            line=self.line,
            severity=self.severity,
            confidence=self.confidence,
            title=self.title,
            body=self.body,
            evidence=[
                f"{e.snapshot}:{e.file}:{e.start_line}-{e.end_line}: {e.observation}"
                for e in self.evidence
            ],
        )


class Coverage(StrictModel):
    category: Category
    status: Literal["reviewed", "limited", "not_applicable"]
    limitations: list[Text] = Field(max_length=50)

    @model_validator(mode="after")
    def explain_limit(self):
        if self.status == "limited" and not self.limitations:
            raise ValueError("Limited coverage must identify missing context")
        if any(MONEY.search(value) for value in self.limitations):
            raise ValueError("Coverage cannot contain model-generated money amounts")
        return self


class InfraReviewResult(StrictModel):
    schema_version: Literal["infra-review/v1"]
    reviewed_head_sha: SHA
    findings: list[InfraFinding] = Field(max_length=10)
    coverage: list[Coverage] = Field(min_length=9, max_length=9)

    @model_validator(mode="after")
    def all_categories(self):
        if {item.category for item in self.coverage} != set(CATEGORIES):
            raise ValueError("Return exactly one coverage entry per category")
        return self


class PricingRecord(BaseModel):
    """Trusted estimates, not model output. JSON decimals may be numeric strings."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=128)
    file: str = Field(min_length=1, max_length=500)
    resource: str = Field(min_length=1, max_length=500)
    base_sha: SHA
    head_sha: SHA
    currency: Literal["USD"]
    monthly_base: Decimal | None = Field(default=None, ge=0)
    monthly_head: Decimal | None = Field(default=None, ge=0)
    region: str = Field(min_length=1)
    assumptions: list[str] = Field(min_length=1)
    comparable: bool
    available: bool
    unsupported_resources: list[str] = Field(default_factory=list)
    source: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    generated_at: datetime

    @model_validator(mode="after")
    def usable_amounts(self):
        if self.available and (self.monthly_base is None or self.monthly_head is None):
            raise ValueError("Available pricing must include both amounts")
        if self.generated_at.tzinfo is None:
            raise ValueError("Pricing timestamp must include a timezone")
        return self

    @property
    def usable(self) -> bool:
        return self.available and self.comparable

    @property
    def delta(self) -> Decimal | None:
        if not self.usable:
            return None
        return self.monthly_head - self.monthly_base


class PricingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["infra-pricing/v1"]
    records: list[PricingRecord] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({record.id for record in self.records}) != len(self.records):
            raise ValueError("Pricing IDs must be unique")
        return self
