"""Live session with an explicit controller: automation | human | none."""

from __future__ import annotations

import asyncio
import json
from enum import Enum
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from capability_runtime.surface.playwright_surface import Surface


class Controller(str, Enum):
    automation = "automation"
    human = "human"
    none = "none"


class LiveSession:
    """One Chromium context. Pause leaves the window open; resume continues the same page."""

    def __init__(
        self,
        *,
        headed: bool = True,
        evidence_dir: Path | None = None,
        resume_poll_s: float = 0.5,
    ) -> None:
        self.headed = headed
        self.evidence_dir = evidence_dir
        self.resume_poll_s = resume_poll_s
        self.controller = Controller.none
        self._pw: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.surface: Surface | None = None
        self.human_actions: list[dict[str, Any]] = []
        self._resume = asyncio.Event()

    @property
    def resume_signal_path(self) -> Path | None:
        if not self.evidence_dir:
            return None
        return self.evidence_dir / "resume.signal"

    async def start(self) -> Surface:
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=not self.headed)
        self.context = await self.browser.new_context(viewport={"width": 1280, "height": 800})
        self.page = await self.context.new_page()
        self.surface = Surface(self.page)
        self.controller = Controller.automation
        await self._bind_human_input_logging()
        return self.surface

    async def _bind_human_input_logging(self) -> None:
        assert self.page is not None

        async def _on_event(payload: dict) -> None:
            self._record_human(payload)

        try:
            await self.page.expose_function("cuaHumanEvent", lambda payload: self._record_human(payload))
        except Exception:
            return

        async def _inject() -> None:
            try:
                await self.page.evaluate(
                    """
                    () => {
                      if (window.__cuaHumanBound) return;
                      window.__cuaHumanBound = true;
                      document.addEventListener('click', (e) => {
                        const t = e.target;
                        try {
                          window.cuaHumanEvent({
                            type: 'click',
                            tag: t && t.tagName,
                            name: t && (t.getAttribute('name') || t.id || (t.innerText || '').slice(0, 40)),
                          });
                        } catch (err) {}
                      }, true);
                    }
                    """
                )
            except Exception:
                pass

        self.page.on("load", lambda _: asyncio.create_task(_inject()))
        await _inject()

    def _record_human(self, payload: Any) -> None:
        if self.controller == Controller.human:
            self.human_actions.append({"kind": "dom", "payload": payload, "actor": "human"})

    async def pause(self, intervention: dict[str, Any]) -> Path:
        """Cede control of the live session. Does not close the browser."""
        self.controller = Controller.human
        self._resume.clear()
        if not self.evidence_dir:
            raise RuntimeError("evidence_dir required for HITL pause")
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self.evidence_dir / "intervention.json"
        if self.surface:
            shot = self.evidence_dir / "intervention.png"
            try:
                await self.surface.screenshot(str(shot))
                intervention = {**intervention, "screenshot": str(shot)}
            except Exception:
                pass
        intervention = {
            **intervention,
            "controller": self.controller.value,
            "url": self.page.url if self.page else None,
            "resume_how": f"python cli.py resume --run-id {self.evidence_dir.name}",
        }
        path.write_text(json.dumps(intervention, indent=2), encoding="utf-8")
        signal = self.resume_signal_path
        if signal and signal.exists():
            signal.unlink()
        return path

    async def wait_for_resume(self, timeout_s: float = 600) -> None:
        """Block until resume.signal exists or signal_resume() is called."""
        deadline = asyncio.get_event_loop().time() + timeout_s
        while True:
            if self._resume.is_set():
                break
            signal = self.resume_signal_path
            if signal and signal.exists():
                break
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("human did not resume in time")
            await asyncio.sleep(self.resume_poll_s)
        self.controller = Controller.automation
        self._resume.set()

    def signal_resume(self) -> None:
        self._resume.set()
        if self.resume_signal_path:
            self.resume_signal_path.parent.mkdir(parents=True, exist_ok=True)
            self.resume_signal_path.write_text("resume", encoding="utf-8")

    async def close(self) -> None:
        self.controller = Controller.none
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()
        self.surface = None
        self.page = None
