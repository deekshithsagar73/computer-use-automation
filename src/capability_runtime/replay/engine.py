"""Deterministic replay. Zero LLM calls."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeout

from capability_runtime.evidence.log import EvidenceLog
from capability_runtime.policy.engine import Policy, PolicyViolation
from capability_runtime.policy.redact import substitute_params
from capability_runtime.schema.capability import Capability, Step
from capability_runtime.schema.outcomes import checkpoint_ok, match_outcome
from capability_runtime.schema.result import RunResult, RunStatus
from capability_runtime.session.live import Controller, LiveSession
from capability_runtime.surface.playwright_surface import Surface


class ReplayEngine:
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
        for step in self.capability.steps:
            try:
                self.policy.check_action(step.type)
                result = await self._execute_step(session, surface, step)
                if result is not None:
                    shot = _shot_name(result)
                    if not (self.evidence_dir / shot).exists():
                        await self._capture(surface, shot)
                    return result
            except PolicyViolation as exc:
                self.log.write({"event": "policy", "step": step.id, "error": str(exc)})
                await self._capture(surface, f"{step.id}-policy.png")
                if self.hitl:
                    resumed = await self._escalate(session, surface, step, str(exc), "policy")
                    if resumed is not None:
                        return resumed
                    continue
                return self._fail(step.id, "policy allow", str(exc))
            except LookupError as exc:
                self.log.write({"event": "locator_miss", "step": step.id, "error": str(exc)})
                await self._capture(surface, f"{step.id}-miss.png")
                if self.hitl:
                    resumed = await self._escalate(session, surface, step, str(exc), "locator_miss")
                    if resumed is not None:
                        return resumed
                    continue
                return self._fail(step.id, "element located", str(exc))
            except PlaywrightTimeout as exc:
                retried = await self._retry_once(surface, step)
                if retried is True:
                    self.recovered.append(f"{step.id}: retried after timeout")
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

        snap = await surface.observe()
        outcome = match_outcome(self.capability, snap.url, snap.text)
        if outcome and outcome.kind == "business_outcome":
            await self._capture(surface, "outcome.png")
            return self._result(RunStatus.business_outcome, outcome.code, step_id=self.capability.steps[-1].id)

        ok, reason = checkpoint_ok(self.capability, snap.url, snap.text, self.outputs)
        if not ok:
            await self._capture(surface, "checkpoint-miss.png")
            return self._fail(self.capability.steps[-1].id, "checkpoint satisfied", reason)

        status = RunStatus.recovered if self.recovered else RunStatus.success
        await self._capture(surface, "final.png")
        return self._result(status, "success")

    async def _execute_step(
        self, session: LiveSession, surface: Surface, step: Step
    ) -> RunResult | None:
        control_name = None
        if step.target:
            control_name = step.target.locators[0].value
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
            value = self._expand(step.value)
            await surface.type_text(step.target, value)
            self.log.write({"event": "act", "actor": "automation", "step": step.id, "type": "type"})
        elif step.type == "extract":
            assert step.extract
            text = await self._extract(surface, step)
            self.outputs[step.extract.field] = text
            self.log.write({"event": "act", "actor": "automation", "step": step.id, "type": "extract", "field": step.extract.field})
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
        try:
            return (await surface.extract_text(step.extract.locators)).strip()
        except LookupError:
            snap = await surface.observe()
            # First-account special case: first overview table link that looks like an id.
            if step.extract.field == "account_id":
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
            if step.type in {"click", "type"} and step.target:
                if step.type == "click":
                    await surface.click(step.target)
                else:
                    await surface.type_text(step.target, self._expand(step.value or ""))
                return True
        except Exception as exc:
            return self._fail(step.id, "retry succeeded", str(exc))
        return None

    async def _escalate(
        self, session: LiveSession, surface: Surface, step: Step, observed: str, why: str
    ) -> RunResult:
        snap = await surface.observe()
        await session.pause(
            {
                "capability": self.capability.id,
                "goal": self.capability.description,
                "step_id": step.id,
                "why": why,
                "expected": step.description or step.type,
                "observed": observed,
                "url": snap.url,
            }
        )
        self.log.write({"event": "escalated", "step": step.id, "why": why, "controller": Controller.human.value})
        try:
            await session.wait_for_resume()
        except TimeoutError as exc:
            await self._capture(surface, "hitl-timeout.png")
            return self._result(RunStatus.escalated, "hitl_timeout", step_id=step.id, error=str(exc), expected=why, observed=observed)
        self.log.write({"event": "resumed", "actor": "human", "human_actions": session.human_actions})
        try:
            retried = await self._execute_step(session, surface, step)
        except Exception as exc:
            await self._capture(surface, "after-human.png")
            return self._result(
                RunStatus.escalated,
                "human_resumed",
                step_id=step.id,
                expected="step succeeded after human",
                observed=str(exc),
            )
        if retried is not None:
            await self._capture(surface, _shot_name(retried))
            return retried
        return None

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


def _shot_name(result: RunResult) -> str:
    if result.status == RunStatus.success:
        return "final.png"
    if result.status == RunStatus.business_outcome:
        return "outcome.png"
    if result.status == RunStatus.escalated:
        return "after-human.png"
    return "failed.png"
