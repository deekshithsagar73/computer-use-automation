"""JSONL evidence log plus failure screenshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from capability_runtime.policy.redact import redact_obj


class EvidenceLog:
    def __init__(self, directory: Path, params: dict[str, Any], secret_names: set[str]) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.params = params
        self.secret_names = secret_names
        self.path = self.directory / "events.jsonl"

    def write(self, event: dict[str, Any]) -> None:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            **redact_obj(event, self.params, self.secret_names),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")

    def write_json(self, name: str, data: Any) -> Path:
        path = self.directory / name
        path.write_text(json.dumps(redact_obj(data, self.params, self.secret_names), indent=2, default=str), encoding="utf-8")
        return path
