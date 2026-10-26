from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    delivery_id: str = Field(min_length=1, max_length=128)
    installation_id: int = Field(gt=0)
    repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    pr: int = Field(gt=0)
    comment_id: int = Field(gt=0)
    commenter: str = Field(min_length=1, max_length=100)
    command: str = Field(min_length=1, max_length=64)
    args: list[str] = Field(default_factory=list, max_length=32)


class ReviewContext(BaseModel):
    repo: str
    pr: int
    base_sha: str
    head_sha: str
    title: str = ""
    description: str = ""
    workspace_path: str | None = None


class Finding(BaseModel):
    reviewer: str
    rule_id: str
    file: str
    line: int | None = Field(default=None, gt=0)
    end_line: int | None = Field(default=None, gt=0)
    severity: Literal["critical", "high", "medium", "low", "info"]
    confidence: float = Field(ge=0, le=1)
    title: str
    body: str
    suggestion: str | None = None
    evidence: list[str] = Field(default_factory=list)
