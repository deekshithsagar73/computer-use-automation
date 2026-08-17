"""Allowlist, action allowlist, and risk classification. Enforced before every act."""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, Field

ALLOWED_ACTIONS = frozenset({"navigate", "click", "type", "extract", "assert"})
INTERNAL_URLS = frozenset({"about:blank", "chrome://new-tab-page/", "chrome://newtab/"})

IRREVERSIBLE_NAME_MARKERS = (
    "transfer",
    "bill pay",
    "billpay",
    "open new account",
    "open account",
    "delete",
    "approve",
    "submit payment",
    "wire",
)


class PolicyViolation(Exception):
    def __init__(self, message: str, *, code: str = "policy_denied") -> None:
        super().__init__(message)
        self.code = code


class Policy(BaseModel):
    allowed_hosts: list[str] = Field(default_factory=lambda: ["parabank.parasoft.com"])
    allowed_actions: list[str] = Field(default_factory=lambda: sorted(ALLOWED_ACTIONS))
    block_irreversible: bool = True

    def host_allowed(self, url: str) -> bool:
        if url in INTERNAL_URLS:
            return True
        parsed = urlparse(url)
        if parsed.scheme in {"data", "blob"}:
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        for allowed in self.allowed_hosts:
            allowed = allowed.lower().lstrip(".")
            if host == allowed or host.endswith("." + allowed):
                return True
        return False

    def check_navigate(self, url: str) -> None:
        if not self.host_allowed(url):
            raise PolicyViolation(
                f"navigate blocked: host not in allowlist ({url})",
                code="host_not_allowed",
            )

    def check_action(self, action_type: str) -> None:
        if action_type not in self.allowed_actions:
            raise PolicyViolation(
                f"action {action_type!r} is not on the allowlist",
                code="action_not_allowed",
            )

    def risk_for_click_name(self, name: str | None) -> str:
        label = (name or "").strip().lower()
        if any(marker in label for marker in IRREVERSIBLE_NAME_MARKERS):
            return "blocked" if self.block_irreversible else "confirm"
        return "safe"

    def check_step_risk(self, risk: str, *, control_name: str | None = None) -> None:
        inferred = self.risk_for_click_name(control_name)
        effective = "blocked" if "blocked" in (risk, inferred) else risk
        if effective == "blocked":
            raise PolicyViolation(
                f"irreversible action blocked ({control_name or risk})",
                code="irreversible_blocked",
            )
