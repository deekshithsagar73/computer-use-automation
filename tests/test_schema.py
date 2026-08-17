from pathlib import Path

from capability_runtime.schema.capability import Capability
from capability_runtime.schema.outcomes import checkpoint_ok, match_outcome
from capability_runtime.schema.result import RunStatus


ROOT = Path(__file__).resolve().parents[1]
CAP = ROOT / "capabilities" / "lookup_balance.v1.json"


def test_capability_round_trip():
    cap = Capability.load(CAP)
    dumped = cap.model_dump_json()
    again = Capability.model_validate_json(dumped)
    assert again.id == "parabank.lookup_balance"
    assert again.schema_version == "1.0"
    assert "password" in again.secret_param_names()
    assert again.steps[0].type == "navigate"


def test_rejects_embedded_password_literal():
    cap = Capability.load(CAP)
    data = cap.model_dump()
    data["steps"][2]["value"] = "password: hunter2"
    try:
        Capability.model_validate(data)
        raised = False
    except Exception:
        raised = True
    assert raised


def test_json_schema_export(tmp_path: Path):
    cap = Capability.load(CAP)
    path = tmp_path / "capability.schema.json"
    cap.to_json_schema_file(path)
    assert "LocatorStrategy" in path.read_text(encoding="utf-8") or "locators" in path.read_text(encoding="utf-8")


def test_invalid_login_is_business_outcome_not_crash():
    cap = Capability.load(CAP)
    hit = match_outcome(
        cap,
        "https://parabank.parasoft.com/parabank/login.htm",
        "Error! Please enter a username and password.",
    )
    assert hit is not None
    assert hit.code == "invalid_login"
    assert hit.kind == "business_outcome"


def test_checkpoint_requires_outputs():
    cap = Capability.load(CAP)
    ok, reason = checkpoint_ok(cap, "https://parabank.parasoft.com/parabank/activity.htm?id=1", "Available Balance", {})
    assert ok is False
    assert "account_id" in reason


def test_checkpoint_success():
    cap = Capability.load(CAP)
    ok, _ = checkpoint_ok(
        cap,
        "https://parabank.parasoft.com/parabank/activity.htm?id=12345",
        "Account Details",
        {"account_id": "12345", "available_balance": "$100.00"},
    )
    assert ok is True


def test_run_status_values():
    assert {s.value for s in RunStatus} == {
        "success",
        "business_outcome",
        "recovered",
        "escalated",
        "failed",
    }
