"""Structured run result. Distinguishes business outcomes from failures."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    success = "success"
    business_outcome = "business_outcome"
    recovered = "recovered"
    escalated = "escalated"
    failed = "failed"


class RunResult(BaseModel):
    status: RunStatus
    outcome_code: str | None = None
    outputs: dict[str, Any] = Field(default_factory=dict)
    step_id: str | None = None
    expected: str | None = None
    observed: str | None = None
    evidence_dir: str | None = None
    llm_calls: int = 0
    error: str | None = None
    recovered_events: list[str] = Field(default_factory=list)

    def is_ok(self) -> bool:
        return self.status in (RunStatus.success, RunStatus.business_outcome, RunStatus.recovered)
