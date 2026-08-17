from capability_runtime.schema.capability import Capability
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_write_json_schema_artifact():
    cap = Capability.load(ROOT / "capabilities" / "lookup_balance.v1.json")
    out = ROOT / "capabilities" / "capability.schema.json"
    cap.to_json_schema_file(out)
    assert out.exists()
    assert "\"title\"" in out.read_text(encoding="utf-8") or "properties" in out.read_text(encoding="utf-8")
