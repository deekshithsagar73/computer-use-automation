import pytest

from capability_runtime.policy.engine import Policy, PolicyViolation
from capability_runtime.policy.redact import redact_text, substitute_params


def test_allowlist_blocks_foreign_host():
    policy = Policy(allowed_hosts=["parabank.parasoft.com"])
    with pytest.raises(PolicyViolation) as exc:
        policy.check_navigate("https://example.com/")
    assert exc.value.code == "host_not_allowed"


def test_allowlist_permits_parabank():
    policy = Policy()
    policy.check_navigate("https://parabank.parasoft.com/parabank/index.htm")


def test_action_allowlist():
    policy = Policy()
    with pytest.raises(PolicyViolation):
        policy.check_action("evaluate_js")
    policy.check_action("click")
    policy.check_action("select")


def test_irreversible_transfer_blocked():
    policy = Policy()
    with pytest.raises(PolicyViolation) as exc:
        policy.check_step_risk("safe", control_name="Transfer Funds")
    assert exc.value.code == "irreversible_blocked"


def test_lookup_click_is_safe():
    policy = Policy()
    policy.check_step_risk("safe", control_name="Log In")


def test_secret_substitution_and_redaction():
    params = {"username": "john", "password": "demo-secret"}
    secrets = {"password"}
    typed = substitute_params("user=${username} pass=${password}", params, secret_names=secrets)
    assert typed == "user=john pass=demo-secret"
    redacted = redact_text(typed, params, secrets)
    assert "demo-secret" not in redacted
    assert "<secret:password>" in redacted


def test_account_numbers_redacted():
    text = redact_text("account 12345678 posted", {}, set())
    assert "12345678" not in text
    assert text.endswith("5678 posted") or "****5678" in text


def test_redact_does_not_corrupt_capability_ids():
    params = {"password": "demo"}
    out = redact_text("parabank.lookup_balance.hitl_demo", params, {"password"})
    assert out == "parabank.lookup_balance.hitl_demo"
