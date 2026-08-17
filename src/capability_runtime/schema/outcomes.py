"""Classify page text/URL into declared capability outcomes."""

from __future__ import annotations

import re

from capability_runtime.schema.capability import Capability, OutcomeDetector


def match_outcome(capability: Capability, url: str, page_text: str) -> OutcomeDetector | None:
    """Return the first matching outcome detector, preferring business_outcome over success."""
    text = page_text or ""
    matches: list[OutcomeDetector] = []
    for detector in capability.outcomes:
        if _detector_hits(detector, url, text):
            matches.append(detector)
    business = [m for m in matches if m.kind == "business_outcome"]
    if business:
        return business[0]
    return matches[0] if matches else None


def checkpoint_ok(capability: Capability, url: str, page_text: str, outputs: dict) -> tuple[bool, str]:
    cp = capability.checkpoint
    if cp.url_regex and not re.search(cp.url_regex, url):
        return False, f"url {url!r} did not match /{cp.url_regex}/"
    if cp.text_contains and cp.text_contains not in (page_text or ""):
        return False, f"page text missing {cp.text_contains!r}"
    for name in cp.required_outputs:
        value = outputs.get(name)
        if value is None or str(value).strip() == "":
            return False, f"missing required output {name!r}"
    return True, "checkpoint satisfied"


def _detector_hits(detector: OutcomeDetector, url: str, text: str) -> bool:
    url_ok = True
    text_ok = True
    if detector.url_regex:
        url_ok = bool(re.search(detector.url_regex, url))
    if detector.text_contains:
        text_ok = detector.text_contains in text
    if detector.url_regex and detector.text_contains:
        return url_ok and text_ok
    if detector.url_regex:
        return url_ok
    if detector.text_contains:
        return text_ok
    return False
