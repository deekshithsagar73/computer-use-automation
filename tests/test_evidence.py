from capability_runtime.evidence.log import EvidenceLog


def test_evidence_redacts_password(tmp_path):
    log = EvidenceLog(tmp_path, {"password": "demo-secret", "username": "john"}, {"password"})
    log.write({"event": "act", "typed": "demo-secret", "account": "12345678"})
    text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "demo-secret" not in text
    assert "<secret:password>" in text
    assert "12345678" not in text
