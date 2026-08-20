"""Redact secrets and account-like digit runs from logs and artifacts."""

from __future__ import annotations

import re
from typing import Any

SECRET_PLACEHOLDER = "<secret:{name}>"

_ACCOUNT_LIKE = re.compile(r"\b(\d{5,})\b")


def substitute_params(template: str, params: dict[str, Any], *, secret_names: set[str]) -> str:
    """Replace ${name} with param values. Missing required keys raise."""
    out = template
    for match in re.finditer(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}", template):
        key = match.group(1)
        if key not in params:
            raise KeyError(f"missing parameter {key}")
        out = out.replace(f"${{{key}}}", str(params[key]))
    return out


def redact_text(text: str, params: dict[str, Any], secret_names: set[str]) -> str:
    redacted = text
    for name in secret_names:
        value = params.get(name)
        if not value:
            continue
        # Whole-token replacement so "demo" does not corrupt ids like hitl_demo.
        pattern = re.compile(r"(?<!\w)" + re.escape(str(value)) + r"(?!\w)")
        redacted = pattern.sub(SECRET_PLACEHOLDER.format(name=name), redacted)
    return redact_account_numbers(redacted)


def redact_account_numbers(text: str) -> str:
    def _keep_last4(match: re.Match[str]) -> str:
        digits = match.group(1)
        if len(digits) <= 4:
            return digits
        return f"****{digits[-4:]}"

    return _ACCOUNT_LIKE.sub(_keep_last4, text)


def redact_obj(obj: Any, params: dict[str, Any], secret_names: set[str]) -> Any:
    if isinstance(obj, str):
        return redact_text(obj, params, secret_names)
    if isinstance(obj, dict):
        return {k: redact_obj(v, params, secret_names) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v, params, secret_names) for v in obj]
    return obj


def looks_like_embedded_secret(text: str) -> bool:
    return bool(re.search(r"(?i)(password|token|ssn)\s*[:=]\s*\S{3,}", text))
