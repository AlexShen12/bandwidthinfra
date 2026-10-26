"""Trusted policy and explicit authentication modes."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class Limits:
    diff_chars: int = 45_000
    context_chars: int = 25_000
    file_chars: int = 12_000
    related_files: int = 8
    max_files: int = 150
    max_changed_lines: int = 5000
    max_output_tokens: int = 8000
    max_blob_bytes: int = 1_000_000


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: bool = True
    min_confidence: float = Field(default=0.85, ge=0.85, le=1)
    max_comments: int = Field(default=10, ge=1, le=10)
    cost_threshold_usd_month: Decimal = Field(default=Decimal("100"), ge=0)
    ignore_paths: list[str] = Field(default_factory=list)
    skip_label: str = "skip-ai-review"


def load_policy(text: str | None) -> tuple[Policy, list[str]]:
    if not text:
        return Policy(), []
    try:
        data = yaml.safe_load(text)
        if not isinstance(data, dict) or data.get("version", 1) != 1:
            raise ValueError("Expected a mapping with version: 1")
        reviewers = data.get("reviewers", {})
        if not isinstance(reviewers, dict):
            raise ValueError("reviewers must be a mapping")
        infra = reviewers.get("infra", {})
        if not isinstance(infra, dict):
            raise ValueError("reviewers.infra must be a mapping")
        values = {
            key: data[key] for key in ("max_comments", "ignore_paths", "skip_label") if key in data
        }
        values.update(infra)
        warnings = [
            f"Unsupported policy key: {key}"
            for key in data
            if key not in {"version", "reviewers", "max_comments", "ignore_paths", "skip_label"}
        ]
        return Policy.model_validate(values), warnings
    except (yaml.YAMLError, ValueError, TypeError) as exc:
        return Policy(), [
            f"Invalid trusted reviewer config; using defaults ({type(exc).__name__})."
        ]


@dataclass(frozen=True)
class Settings:
    region: str = "us-west-2"
    model_id: str = "openai.gpt-oss-120b-1:0"
    mode: Literal["fake", "real"] = "fake"
    auth_mode: Literal["api-key", "iam"] = "api-key"

    @classmethod
    def from_env(cls, env):
        mode, auth = env.get("BEDROCK", "fake"), env.get("BEDROCK_AUTH_MODE", "api-key")
        if mode not in {"fake", "real"} or auth not in {"api-key", "iam"}:
            raise ValueError("BEDROCK must be fake/real and BEDROCK_AUTH_MODE must be api-key/iam")
        return cls(
            region=env.get("AWS_REGION") or "us-west-2",
            model_id=env.get("BEDROCK_MODEL_ID") or "openai.gpt-oss-120b-1:0",
            mode=mode,
            auth_mode=auth,
        )
