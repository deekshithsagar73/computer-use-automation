import json
from pathlib import Path

from capability_runtime.cli import cmd_resume
from capability_runtime.session.live import Controller, LiveSession


class _Args:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id


async def test_pause_resume_same_session(tmp_path: Path):
    session = LiveSession(headed=False, evidence_dir=tmp_path, resume_poll_s=0.05)
    await session.start()
    try:
        assert session.page is not None
        await session.page.goto("about:blank")
        assert session.controller is Controller.automation
        path = await session.pause(
            {
                "capability": "test",
                "goal": "pause test",
                "step_id": "s1",
                "why": "stuck",
                "expected": "next step",
                "observed": "locator miss",
            }
        )
        assert path.exists()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["controller"] == "human"
        assert payload["why"] == "stuck"
        assert session.controller is Controller.human
        session.signal_resume()
        await session.wait_for_resume(timeout_s=5)
        assert session.controller is Controller.automation
        assert session.page is not None
        assert session.page.url.startswith("about:blank")
    finally:
        await session.close()


def test_resume_cli_writes_signal(tmp_path: Path, monkeypatch):
    run = tmp_path / "evidence" / "replay-error" / "run1"
    run.mkdir(parents=True)
    (run / "intervention.json").write_text("{}", encoding="utf-8")
    import capability_runtime.cli as cli

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cmd_resume(_Args("run1")) == 0
    assert (run / "resume.signal").read_text(encoding="utf-8") == "resume"
