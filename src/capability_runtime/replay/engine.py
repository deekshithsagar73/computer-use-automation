"""Deterministic replay interpreter. Never calls an LLM (llm_calls stays 0)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import asyncio
import os

from playwright.async_api import TimeoutError as PlaywrightTimeout

from capability_runtime.evidence.log import EvidenceLog
from capability_runtime.policy.engine import Policy, PolicyViolation
from capability_runtime.policy.redact import substitute_params
from capability_runtime.schema.capability import Capability, Step, Target
from capability_runtime.schema.outcomes import checkpoint_ok, match_outcome
from capability_runtime.schema.result import RunResult, RunStatus
from capability_runtime.session.live import Controller, LiveSession
from capability_runtime.surface.playwright_surface import Surface


class ReplayEngine:
    """Walk a capability.v1 file: policy gate, locator ladder, outcomes, optional HITL."""

    def __init__(
        self,
        capability: Capability,
        params: dict[str, Any],
        policy: Policy,
        *,
        hitl: bool = False,
        headed: bool = True,
        evidence_dir: Path,
    ) -> None:
        self.capability = capability
        self.params = dict(params)
        self.policy = policy
        self.hitl = hitl
        self.headed = headed
        self.evidence_dir = evidence_dir
        self.secrets = capability.secret_param_names()
        self.log = EvidenceLog(evidence_dir, self.params, self.secrets)
        self.outputs: dict[str, Any] = {}
        self.recovered: list[str] = []

    def _expand(self, template: str) -> str:
        merged = {**self.params, "entry_url": self.capability.app.entry_url}
        return substitute_params(template, merged, secret_names=self.secrets)

    async def run(self) -> RunResult:
        session = LiveSession(headed=self.headed, evidence_dir=self.evidence_dir)
        surface = await session.start()
        self.log.write({"event": "replay_start", "capability": self.capability.id, "llm_calls": 0})
        try:
            return await self._run_steps(session, surface)
        finally:
            await session.close()

    async def _run_steps(self, session: LiveSession, surface: Surface) -> RunResult:
        steps = self.capability.steps
        index = 0
        while index < len(steps):
            step = steps[index]
            try:
                self.policy.check_action(step.type)
                result = await self._execute_step(session, surface, step)
                if result is not None:
                    await self._capture_if_missing(surface, _shot_name(result))
                    return result
            except PolicyViolation as exc:
                self.log.write({"event": "policy", "step": step.id, "error": str(exc)})
                await self._capture(surface, f"{step.id}-policy.png")
                if self.hitl:
                    handoff = await self._escalate(session, surface, step, str(exc), "policy", index)
                    if handoff is not None:
                        return handoff
                    index += 1
                    continue
                return self._fail(step.id, "policy allow", str(exc))
            except LookupError as exc:
                self.log.write({"event": "locator_miss", "step": step.id, "error": str(exc)})
                await self._capture(surface, f"{step.id}-miss.png")
                if self.hitl:
                    handoff = await self._escalate(session, surface, step, str(exc), "locator_miss", index)
                    if handoff is not None:
                        return handoff
                    index += 1
                    continue
                return self._fail(step.id, "element located", str(exc))
            except PlaywrightTimeout as exc:
                retried = await self._retry_once(surface, step)
                if retried is True:
                    self.recovered.append(f"{step.id}: retried after timeout")
                    index += 1
                    continue
                if retried is not None:
                    await self._capture(surface, f"{step.id}-timeout.png")
                    return retried
                await self._capture(surface, f"{step.id}-timeout.png")
                return self._fail(step.id, "step completed before timeout", str(exc))
            except Exception as exc:
                await self._capture(surface, f"{step.id}-error.png")
                self.log.write({"event": "error", "step": step.id, "error": str(exc)})
                return self._fail(step.id, "step succeeded", str(exc))
            index += 1

        snap = await surface.observe()
        outcome = match_outcome(self.capability, snap.url, snap.text)
        if outcome and outcome.kind == "business_outcome":
            await self._capture(surface, "outcome.png")
            return self._result(RunStatus.business_outcome, outcome.code, step_id=steps[-1].id)

        ok, reason = checkpoint_ok(self.capability, snap.url, snap.text, self.outputs)
        if not ok:
            await self._capture(surface, "checkpoint-miss.png")
            return self._fail(steps[-1].id, "checkpoint satisfied", reason)

        status = RunStatus.recovered if self.recovered else RunStatus.success
        code = "success_after_human" if any("human" in r for r in self.recovered) else "success"
        await self._capture(surface, "final.png")
        return self._result(status, code)

    async def _execute_step(
        self, session: LiveSession, surface: Surface, step: Step
    ) -> RunResult | None:
        control_name = step.target.locators[0].value if step.target and step.target.locators else None
        self.policy.check_step_risk(step.risk, control_name=control_name)

        if step.type == "navigate":
            url = self._expand(step.url or "")
            self.policy.check_navigate(url)
            await surface.goto(url)
            self.log.write({"event": "act", "actor": "automation", "step": step.id, "type": "navigate", "url": url})
        elif step.type == "click":
            assert step.target
            await surface.click(step.target)
            self.log.write({"event": "act", "actor": "automation", "step": step.id, "type": "click"})
        elif step.type == "type":
            assert step.target and step.value is not None
            await surface.type_text(step.target, self._expand(step.value))
            self.log.write({"event": "act", "actor": "automation", "step": step.id, "type": "type"})
        elif step.type == "select":
            assert step.target and step.value is not None
            chosen = self._expand(step.value)
            await surface.select_option(step.target, chosen)
            if "account" in (step.description or "").lower():
                self.outputs.setdefault("account_id", chosen)
            self.log.write({"event": "act", "actor": "automation", "step": step.id, "type": "select"})
        elif step.type == "extract":
            assert step.extract
            text = await self._extract(surface, step)
            self.outputs[step.extract.field] = text
            self.log.write(
                {"event": "act", "actor": "automation", "step": step.id, "type": "extract", "field": step.extract.field}
            )
        elif step.type == "assert":
            snap = await surface.observe()
            assertion = step.assertion
            assert assertion
            if assertion.url_regex and not re.search(assertion.url_regex, snap.url):
                await self._capture(surface, f"{step.id}-assert.png")
                return self._fail(step.id, f"url /{assertion.url_regex}/", snap.url)
            if assertion.text_contains and assertion.text_contains not in snap.text:
                await self._capture(surface, f"{step.id}-assert.png")
                return self._fail(step.id, f"text {assertion.text_contains!r}", "not found")
            self.log.write({"event": "act", "actor": "automation", "step": step.id, "type": "assert"})

        snap = await surface.observe()
        outcome = match_outcome(self.capability, snap.url, snap.text)
        if outcome and outcome.kind == "business_outcome":
            self.log.write({"event": "outcome", "code": outcome.code, "step": step.id})
            await self._capture(surface, "outcome.png")
            return self._result(RunStatus.business_outcome, outcome.code, step_id=step.id)
        return None

    async def _extract(self, surface: Surface, step: Step) -> str:
        assert step.extract
        field = step.extract.field
        if field == "transaction_count":
            loc = await surface.resolve(Target(locators=step.extract.locators))
            return str(await loc.count())

        try:
            return (await surface.extract_text(step.extract.locators)).strip()
        except LookupError:
            snap = await surface.observe()
            if field == "account_id":
                for control in snap.controls:
                    if control.role == "link" and re.fullmatch(r"\d+", control.name or ""):
                        return control.name
            raise

    async def _retry_once(self, surface: Surface, step: Step) -> RunResult | bool | None:
        try:
            await surface.page.wait_for_timeout(1500)
            if step.type == "navigate" and step.url:
                await surface.goto(self._expand(step.url))
                return True
            if step.type in {"click", "type", "select"} and step.target:
                if step.type == "click":
                    await surface.click(step.target)
                elif step.type == "select":
                    await surface.select_option(step.target, self._expand(step.value or ""))
                else:
                    await surface.type_text(step.target, self._expand(step.value or ""))
                return True
        except Exception as exc:
            return self._fail(step.id, "retry succeeded", str(exc))
        return None

    async def _escalate(
        self,
        session: LiveSession,
        surface: Surface,
        step: Step,
        observed: str,
        why: str,
        step_index: int,
    ) -> RunResult | None:
        """Pause for a human operator, then retry or skip if they already advanced the UI."""
        snap = await surface.observe()
        instructions = _human_instructions(step)
        await session.pause(
            {
                "capability": self.capability.id,
                "goal": self.capability.description,
                "step_id": step.id,
                "why": why,
                "expected": step.description or step.type,
                "observed": observed,
                "url": snap.url,
                "instructions": instructions,
            }
        )
        self.log.write({"event": "escalated", "step": step.id, "why": why, "controller": Controller.human.value})
        print(
            f"\n[HITL] Automation paused at step {step.id}. "
            f"Use the Chromium window: {instructions}\n"
            f"Then run: python cli.py resume --run-id {self.evidence_dir.name}\n",
            flush=True,
        )
        if os.getenv("CUA_SIMULATE_HUMAN") == "1":
            asyncio.create_task(self._simulate_human_action(session, surface, step))
        try:
            await session.wait_for_resume()
        except TimeoutError as exc:
            await self._capture(surface, "hitl-timeout.png")
            return self._result(
                RunStatus.escalated, "hitl_timeout", step_id=step.id, error=str(exc), expected=why, observed=observed
            )

        self.log.write({"event": "resumed", "actor": "human", "human_actions": session.human_actions})

        if await self._step_obviated(surface, step):
            self.recovered.append(f"{step.id}: human completed step in browser")
            self.log.write({"event": "human_completed_step", "step": step.id, "url": surface.page.url})
            await self._capture(surface, "after-human.png")
            return None

        try:
            result = await self._execute_step(session, surface, step)
            if result is not None:
                await self._capture_if_missing(surface, _shot_name(result))
                return result
            return None
        except LookupError as exc:
            if await self._step_obviated(surface, step):
                self.recovered.append(f"{step.id}: human completed step after retry miss")
                self.log.write({"event": "human_completed_step", "step": step.id, "url": surface.page.url})
                await self._capture(surface, "after-human.png")
                return None
            await self._capture(surface, "after-human.png")
            return self._result(
                RunStatus.escalated,
                "human_resumed",
                step_id=step.id,
                expected="step succeeded after human",
                observed=str(exc),
            )

    async def _step_obviated(self, surface: Surface, step: Step) -> bool:
        """True when the human already achieved this step's intent (e.g. opened the account page)."""
        snap = await surface.observe()
        url = snap.url
        desc = (step.description or "").lower()

        if step.type == "click":
            if re.search(r"activity\.htm", url) and any(k in desc for k in ("account", "first account", "open")):
                return True
            if "find transactions" in desc and re.search(r"findtrans\.htm", url) and "Transaction Results" in snap.text:
                return True
            if "findbydaterange" in desc.lower() and "Transaction Results" in snap.text:
                return True

        checkpoint = self.capability.checkpoint.url_regex
        if checkpoint and re.search(checkpoint, url):
            required = self.capability.checkpoint.required_outputs or []
            if not required:
                return True
            if all(self.outputs.get(name) for name in required):
                return True
        return False

    async def _simulate_human_action(self, session: LiveSession, surface: Surface, step: Step) -> None:
        """Optional dev helper: complete the paused step in headless evidence runs (set CUA_SIMULATE_HUMAN=1)."""
        await asyncio.sleep(1.5)
        if session.controller != Controller.human:
            return
        desc = (step.description or "").lower()
        if step.type == "click" and "account" in desc:
            account = str(self.outputs.get("account_id") or self.params.get("account_id") or "13344")
            url = f"https://parabank.parasoft.com/parabank/activity.htm?id={account}"
            self.policy.check_navigate(url)
            await surface.goto(url)
            self.log.write({"event": "simulated_human", "step": step.id, "url": url})
        session.signal_resume()

    def _fail(self, step_id: str, expected: str, observed: str) -> RunResult:
        return self._result(
            RunStatus.failed,
            "failed",
            step_id=step_id,
            expected=expected,
            observed=observed,
            error=observed,
        )

    def _result(
        self,
        status: RunStatus,
        outcome_code: str | None,
        *,
        step_id: str | None = None,
        expected: str | None = None,
        observed: str | None = None,
        error: str | None = None,
    ) -> RunResult:
        result = RunResult(
            status=status,
            outcome_code=outcome_code,
            outputs=self.outputs,
            step_id=step_id,
            expected=expected,
            observed=observed,
            evidence_dir=str(self.evidence_dir),
            llm_calls=0,
            error=error,
            recovered_events=list(self.recovered),
        )
        self.log.write_json("result.json", result.model_dump(mode="json"))
        self.log.write({"event": "replay_end", "status": status.value, "llm_calls": 0})
        return result

    async def _capture(self, surface: Surface, name: str) -> None:
        path = self.evidence_dir / name
        try:
            await surface.screenshot(str(path))
            self.log.write({"event": "screenshot", "path": name})
        except Exception as exc:
            self.log.write({"event": "screenshot_failed", "error": str(exc)})

    async def _capture_if_missing(self, surface: Surface, name: str) -> None:
        if not (self.evidence_dir / name).exists():
            await self._capture(surface, name)


def _shot_name(result: RunResult) -> str:
    if result.status == RunStatus.success:
        return "final.png"
    if result.status == RunStatus.business_outcome:
        return "outcome.png"
    if result.status == RunStatus.escalated:
        return "after-human.png"
    return "failed.png"


def _human_instructions(step: Step) -> str:
    desc = step.description or step.type
    if step.type == "click" and "account" in desc.lower():
        return "Click the first account number link in the Accounts Overview table."
    return f"Complete this step manually: {desc}"
