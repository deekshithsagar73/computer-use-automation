"""Capability v1 — the reviewable, agent-invocable contract.

Decoupled from the model transcript. Replay reads this file and never calls an LLM.
Locator indices from a live snapshot are runtime-only and must not appear here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"

LocatorType = Literal["role", "label", "placeholder", "name", "id", "text", "css", "xpath"]
StepType = Literal["navigate", "click", "type", "extract", "assert"]
RiskClass = Literal["safe", "confirm", "blocked"]
ParamType = Literal["string", "number", "bool"]
OutcomeKind = Literal["success", "business_outcome"]

_SECRET_LITERAL_RE = re.compile(
    r"(?i)(password|passwd|secret|token|ssn|social.?security)\s*[:=]\s*['\"]?[^'\"\s]{3,}"
)


class LocatorStrategy(BaseModel):
    """One way to find a control. Replay tries locators in list order."""

    type: LocatorType
    value: str = Field(..., min_length=1, description="Name, text, selector, or attribute value.")
    role: str | None = Field(
        default=None,
        description="ARIA/HTML role when type=role, e.g. textbox, button, link.",
    )


class Target(BaseModel):
    locators: list[LocatorStrategy] = Field(..., min_length=1)
    description: str | None = None


class ExtractSpec(BaseModel):
    field: str = Field(..., description="Output key to populate.")
    locators: list[LocatorStrategy] = Field(..., min_length=1)


class AssertSpec(BaseModel):
    url_regex: str | None = None
    text_contains: str | None = None


class Step(BaseModel):
    id: str
    type: StepType
    description: str | None = None
    risk: RiskClass = "safe"
    url: str | None = Field(default=None, description="Navigate target. May contain ${param} refs.")
    target: Target | None = None
    value: str | None = Field(default=None, description="Text to type. May contain ${param} refs.")
    extract: ExtractSpec | None = None
    assertion: AssertSpec | None = None

    @model_validator(mode="after")
    def _shape_matches_type(self) -> Step:
        if self.type == "navigate" and not self.url:
            raise ValueError("navigate steps require url")
        if self.type in ("click", "type") and self.target is None:
            raise ValueError(f"{self.type} steps require target")
        if self.type == "type" and self.value is None:
            raise ValueError("type steps require value")
        if self.type == "extract" and self.extract is None:
            raise ValueError("extract steps require extract")
        if self.type == "assert" and self.assertion is None:
            raise ValueError("assert steps require assertion")
        return self


class ParamDef(BaseModel):
    name: str
    type: ParamType = "string"
    secret: bool = False
    required: bool = True
    description: str | None = None


class OutputDef(BaseModel):
    name: str
    type: str = "string"
    description: str | None = None


class OutcomeDetector(BaseModel):
    """Known business result, not a crash. Evaluated after each step."""

    code: str
    kind: OutcomeKind
    url_regex: str | None = None
    text_contains: str | None = None
    description: str | None = None


class Checkpoint(BaseModel):
    url_regex: str | None = None
    text_contains: str | None = None
    required_outputs: list[str] = Field(default_factory=list)


class AppRef(BaseModel):
    """Seam for later tenant overlays: same vendor product, different institution."""

    vendor: str
    product: str
    entry_url: str


class Capability(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    id: str
    name: str
    description: str
    version: str = "1.0.0"
    app: AppRef
    params: list[ParamDef] = Field(default_factory=list)
    outputs: list[OutputDef] = Field(default_factory=list)
    steps: list[Step] = Field(..., min_length=1)
    checkpoint: Checkpoint
    outcomes: list[OutcomeDetector] = Field(default_factory=list)

    @field_validator("steps")
    @classmethod
    def _no_raw_secrets_in_steps(cls, steps: list[Step]) -> list[Step]:
        dumped = json.dumps([s.model_dump() for s in steps])
        if _SECRET_LITERAL_RE.search(dumped):
            raise ValueError("capability must not embed raw secrets; use ${param} refs")
        return steps

    def secret_param_names(self) -> set[str]:
        return {p.name for p in self.params if p.secret}

    def param_names(self) -> set[str]:
        return {p.name for p in self.params}

    def to_json_schema_file(self, path: Path) -> None:
        path.write_text(json.dumps(self.model_json_schema(), indent=2), encoding="utf-8")

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Capability:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def dump_public(self) -> dict[str, Any]:
        """Serialize with no secret values (there should be none)."""
        return self.model_dump(mode="json")
