from capability_runtime.schema.capability import Capability
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_handwritten_capability_has_no_secret_literals():
    text = (ROOT / "capabilities" / "lookup_balance.v1.json").read_text(encoding="utf-8")
    assert "demo" not in text
    assert "${password}" in text
    cap = Capability.load(ROOT / "capabilities" / "lookup_balance.v1.json")
    assert cap.app.product == "parabank"
    assert all(s.risk in {"safe", "confirm", "blocked"} for s in cap.steps)
