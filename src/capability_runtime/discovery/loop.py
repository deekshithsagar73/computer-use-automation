"""LLM observe → decide → act loop. Compiles a successful run into capability.v1."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from capability_runtime.evidence.log import EvidenceLog
from capability_runtime.llm.client import AgentAction, LLMClient, build_llm
from capability_runtime.policy.engine import Policy, PolicyViolation
from capability_runtime.schema.capability import (
    AppRef,
    AssertSpec,
    Capability,
    Checkpoint,
    ExtractSpec,
    LocatorStrategy,
    OutcomeDetector,
    OutputDef,
    ParamDef,
    Step,
    Target,
)
from capability_runtime.schema.result import RunResult, RunStatus
from capability_runtime.session.live import LiveSession
from capability_runtime.surface.playwright_surface import ObservedControl, Snapshot, Surface

SYSTEM = """You operate a legacy banking web UI. You receive a numbered list of controls.
Return ONE JSON object with keys: thought, type, index, text, url, field, outputs, reason.
Allowed type values: click, type, navigate, extract, done, fail.
Rules:
- Only use control indexes from the snapshot.
- Never invent URLs outside the current host.
- Do not click Transfer Funds, Bill Pay, Open New Account, or Update Contact.
- After a successful login, open the FIRST account in the accounts table and read Available Balance.
- When you can see the available balance, call type=done with outputs.account_id and outputs.available_balance.
- If login fails, call type=done with outputs.error describing the message (still a finished run).
- Prefer typing into name=username and name=password fields.
- Keep actions minimal. No screenshots. No JavaScript.
"""


class DiscoveryRun:
    def __init__(
        self,
        goal: str,
        start_url: str,
        params: dict[str, Any],
        policy: Policy,
        evidence_dir: Path,
        *,
        llm: LLMClient | None = None,
        headed: bool = True,
        max_steps: int = 25,
        hitl: bool = False,
    ) -> None:
        self.goal = goal
        self.start_url = start_url
        self.params = params
        self.policy = policy
        self.evidence_dir = evidence_dir
        self.llm = llm or build_llm()
        self.headed = headed
        self.max_steps = max_steps
        self.hitl = hitl
        self.secrets = {k for k in params if k in {"password", "token", "secret"}}
        self.log = EvidenceLog(evidence_dir, params, self.secrets)
        self.trace: list[dict[str, Any]] = []
        self.llm_calls = 0
        self._recent_hashes: list[str] = []

    async def run(self) -> tuple[RunResult, Capability | None]:
        session = LiveSession(headed=self.headed, evidence_dir=self.evidence_dir)
        surface = await session.start()
        try:
            self.policy.check_navigate(self.start_url)
            await surface.goto(self.start_url)
            self.trace.append({"type": "navigate", "url": self.start_url, "control": None})
            self.log.write({"event": "discover_start", "goal": self.goal, "url": self.start_url})

            for step_n in range(1, self.max_steps + 1):
                snap = await surface.observe()
                action = await self._decide(snap, step_n)
                self.llm_calls += 1
                self.log.write({"event": "decide", "step": step_n, "action": action.model_dump(), "url": snap.url})

                if self._is_loop(action):
                    return await self._stop_loop(session, surface, step_n, action)

                try:
                    done = await self._act(surface, snap, action)
                except PolicyViolation as exc:
                    self.log.write({"event": "policy", "error": str(exc)})
                    if self.hitl:
                        await session.pause({"why": str(exc), "step": step_n, "goal": self.goal})
                        await session.wait_for_resume()
                        continue
                    return self._fail(step_n, str(exc)), None
                except Exception as exc:
                    shot = self.evidence_dir / f"step-{step_n}.png"
                    await surface.screenshot(str(shot))
                    self.log.write({"event": "error", "step": step_n, "error": str(exc)})
                    if self.hitl:
                        await session.pause({"why": str(exc), "step": step_n, "goal": self.goal, "screenshot": str(shot)})
                        await session.wait_for_resume()
                        continue
                    return self._fail(step_n, str(exc)), None

                if done is not None:
                    try:
                        shot = self.evidence_dir / (
                            "outcome.png" if done.status == RunStatus.business_outcome else "final.png"
                        )
                        await surface.screenshot(str(shot))
                        self.log.write({"event": "screenshot", "path": shot.name})
                    except Exception as exc:
                        self.log.write({"event": "screenshot_failed", "error": str(exc)})
                    cap = None
                    if done.status == RunStatus.success:
                        cap = compile_capability(self.goal, self.start_url, self.params, self.trace, done.outputs)
                        cap.save(self.evidence_dir / "capability.json")
                    self.log.write_json("result.json", done.model_dump(mode="json"))
                    self.log.write_json("trace.json", self.trace)
                    return done, cap

            return self._fail(self.max_steps, "max steps reached"), None
        finally:
            await session.close()

    async def _decide(self, snap: Snapshot, step_n: int) -> AgentAction:
        from capability_runtime.policy.redact import redact_obj

        recent = redact_obj(self.trace[-8:], self.params, self.secrets)
        user = (
            f"GOAL: {self.goal}\n"
            f"STEP: {step_n}/{self.max_steps}\n"
            f"PARAMS (non-secret): { {k: v for k, v in self.params.items() if k not in self.secrets} }\n"
            f"ACTIONS ALREADY TAKEN (do not repeat a finished type/click): {json.dumps(recent, default=str)}\n"
            f"If a field already has value=, skip it. After username, type password. After both fields, click Log In. Then open the first numeric account link.\n"
            f"{snap.prompt_text()}"
        )
        return await self.llm.complete(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ]
        )

    async def _act(self, surface: Surface, snap: Snapshot, action: AgentAction) -> RunResult | None:
        kind = (action.type or "").lower().strip()
        if kind == "fail":
            return self._fail(None, action.reason or action.thought)

        if kind == "done":
            outputs = dict(action.outputs or {})
            snap = await surface.observe()
            if "could not be verified" in snap.text.lower() or "please enter a username and password" in snap.text.lower():
                result = RunResult(
                    status=RunStatus.business_outcome,
                    outcome_code="invalid_login",
                    outputs=outputs,
                    evidence_dir=str(self.evidence_dir),
                    llm_calls=self.llm_calls,
                )
                return result
            def _blank(value: Any) -> bool:
                return not value or str(value).lower() in {"seen", "unknown", "n/a"}

            if _blank(outputs.get("available_balance")):
                match = re.search(r"Available Balance.*?(\$[\d,]+\.\d{2})", snap.text, re.I | re.S)
                if match:
                    outputs["available_balance"] = match.group(1)
            if _blank(outputs.get("account_id")):
                for control in snap.controls:
                    if control.role == "link" and re.fullmatch(r"\d+", control.name or ""):
                        outputs["account_id"] = control.name
                        break
                if _blank(outputs.get("account_id")):
                    match = re.search(r"Account Number[:\s]+(\d+)", snap.text, re.I)
                    if match:
                        outputs["account_id"] = match.group(1)
            result = RunResult(
                status=RunStatus.success,
                outcome_code="success",
                outputs=outputs,
                evidence_dir=str(self.evidence_dir),
                llm_calls=self.llm_calls,
            )
            return result

        if kind == "navigate":
            self.policy.check_action("navigate")
            url = action.url or self.start_url
            self.policy.check_navigate(url)
            await surface.goto(url)
            self.trace.append({"type": "navigate", "url": url, "control": None})
            return None

        if kind in {"click", "type", "extract"}:
            self.policy.check_action(kind)
            if action.index is None:
                raise ValueError("index required")
            control = snap.by_index(action.index)
            self.policy.check_step_risk("safe", control_name=control.name)
            target = Target(locators=control.locators() or [LocatorStrategy(type="text", value=control.name)], description=control.name)
            if kind == "click":
                await surface.click(target)
                self.trace.append({"type": "click", "control": _control_dict(control)})
            elif kind == "type":
                raw = action.text or ""
                if raw in {"PASSWORD_PLACEHOLDER", "${password}"} or raw == "":
                    raw = str(self.params.get("password", raw))
                if control.input_name == "username" and not raw:
                    raw = str(self.params.get("username", ""))
                if control.input_name == "password":
                    raw = str(self.params.get("password", raw))
                await surface.type_text(target, raw)
                self.trace.append({"type": "type", "control": _control_dict(control), "value": raw})
            else:
                text = await surface.extract_text(target.locators)
                field = action.field or "value"
                self.trace.append({"type": "extract", "control": _control_dict(control), "field": field, "value": text})
            return None

        raise PolicyViolation(f"unknown action {action.type}", code="action_not_allowed")

    def _is_loop(self, action: AgentAction) -> bool:
        h = hashlib.sha256(f"{action.type}|{action.index}|{action.text}".encode()).hexdigest()[:12]
        self._recent_hashes.append(h)
        self._recent_hashes = self._recent_hashes[-8:]
        return self._recent_hashes.count(h) >= 4

    async def _stop_loop(self, session: LiveSession, surface: Surface, step_n: int, action: AgentAction) -> tuple[RunResult, None]:
        shot = self.evidence_dir / "loop.png"
        await surface.screenshot(str(shot))
        self.log.write({"event": "loop_detected", "step": step_n, "action": action.model_dump()})
        if self.hitl:
            await session.pause({"why": "repeated the same action", "step": step_n, "goal": self.goal})
            return RunResult(
                status=RunStatus.escalated,
                outcome_code="stuck_loop",
                step_id=str(step_n),
                evidence_dir=str(self.evidence_dir),
                llm_calls=self.llm_calls,
            ), None
        return self._fail(step_n, "repeated the same action"), None

    def _fail(self, step: int | None, observed: str) -> RunResult:
        result = RunResult(
            status=RunStatus.failed,
            outcome_code="failed",
            step_id=str(step) if step is not None else None,
            expected="progress toward the goal",
            observed=observed,
            error=observed,
            evidence_dir=str(self.evidence_dir),
            llm_calls=self.llm_calls,
        )
        self.log.write_json("result.json", result.model_dump(mode="json"))
        return result


def _control_dict(control: ObservedControl) -> dict[str, Any]:
    return {
        "index": control.index,
        "role": control.role,
        "name": control.name,
        "id": control.id,
        "input_name": control.input_name,
        "placeholder": control.placeholder,
        "tag": control.tag,
        "type": control.type,
        "href": control.href,
    }


def compile_capability(
    goal: str,
    start_url: str,
    params: dict[str, Any],
    trace: list[dict[str, Any]],
    outputs: dict[str, Any],
) -> Capability:
    """Turn a successful discovery trace into a reviewable capability. No transcript."""
    steps: list[Step] = []
    username = str(params.get("username", ""))
    password = str(params.get("password", ""))

    for i, item in enumerate(trace, start=1):
        sid = f"s{i}"
        kind = item["type"]
        control = item.get("control")
        if kind == "navigate":
            url = item["url"]
            if url == start_url:
                url = "${entry_url}"
            steps.append(Step(id=sid, type="navigate", url=url, description="Open application", risk="safe"))
            continue
        locators = _locators_from_control(control or {})
        name = (control or {}).get("name")
        if kind == "click" and _is_account_number_link(control or {}):
            locators = [
                LocatorStrategy(type="css", value="#accountTable tbody tr td a"),
                LocatorStrategy(type="xpath", value="//table[@id='accountTable']//a[1]"),
            ]
            name = "first account"
        target = Target(locators=locators, description=name)
        if kind == "click":
            steps.append(Step(id=sid, type="click", target=target, description=f"Click {name}", risk="safe"))
        elif kind == "type":
            value = str(item.get("value") or "")
            if password and value == password:
                value = "${password}"
            elif username and value == username:
                value = "${username}"
            steps.append(Step(id=sid, type="type", target=target, value=value, description=f"Type into {(control or {}).get('name')}", risk="safe"))
        elif kind == "extract":
            field = item.get("field") or "value"
            steps.append(
                Step(
                    id=sid,
                    type="extract",
                    extract=ExtractSpec(field=field, locators=locators),
                    description=f"Extract {field}",
                    risk="safe",
                )
            )

    output_defs = [OutputDef(name=k, type="string") for k in outputs if k != "error"]
    if not any(o.name == "available_balance" for o in output_defs):
        output_defs.append(OutputDef(name="available_balance", type="string"))
    if not any(o.name == "account_id" for o in output_defs):
        output_defs.append(OutputDef(name="account_id", type="string"))

    steps.append(
        Step(
            id=f"s{len(steps)+1}",
            type="extract",
            extract=ExtractSpec(
                field="account_id",
                locators=[
                    LocatorStrategy(type="id", value="accountId"),
                    LocatorStrategy(type="css", value="#accountId"),
                ],
            ),
            description="Extract account id",
            risk="safe",
        )
    )
    steps.append(
        Step(
            id=f"s{len(steps)+1}",
            type="extract",
            extract=ExtractSpec(
                field="available_balance",
                locators=[
                    LocatorStrategy(type="id", value="availableBalance"),
                    LocatorStrategy(type="css", value="#availableBalance"),
                ],
            ),
            description="Extract available balance",
            risk="safe",
        )
    )
    steps.append(
        Step(
            id=f"s{len(steps)+1}",
            type="assert",
            assertion=AssertSpec(url_regex=r"activity\.htm|overview\.htm"),
            description="Confirm account activity or overview",
            risk="safe",
        )
    )

    return Capability(
        id="parabank.lookup_balance",
        name="Lookup ParaBank account balance",
        description=goal,
        version="1.0.0",
        app=AppRef(vendor="parasoft", product="parabank", entry_url=start_url),
        params=[
            ParamDef(name="username", type="string", secret=False, required=True),
            ParamDef(name="password", type="string", secret=True, required=True),
        ],
        outputs=output_defs,
        steps=steps,
        checkpoint=Checkpoint(
            url_regex=r"activity\.htm|overview\.htm",
            required_outputs=[o.name for o in output_defs],
        ),
        outcomes=[
            OutcomeDetector(
                code="invalid_login",
                kind="business_outcome",
                text_contains="could not be verified",
                description="Username or password was rejected.",
            ),
            OutcomeDetector(
                code="success",
                kind="success",
                url_regex=r"activity\.htm|overview\.htm",
            ),
        ],
    )


def _is_account_number_link(control: dict[str, Any]) -> bool:
    name = str(control.get("name") or "")
    return control.get("role") == "link" and bool(re.fullmatch(r"\d+", name))


def _locators_from_control(control: dict[str, Any]) -> list[LocatorStrategy]:
    locators: list[LocatorStrategy] = []
    role = control.get("role")
    name = control.get("name") or ""
    if role and name:
        locators.append(LocatorStrategy(type="role", value=name, role=role))
    if control.get("placeholder"):
        locators.append(LocatorStrategy(type="placeholder", value=control["placeholder"]))
    if control.get("input_name"):
        locators.append(LocatorStrategy(type="name", value=control["input_name"]))
    if control.get("id"):
        locators.append(LocatorStrategy(type="id", value=control["id"]))
    if name and role == "link":
        locators.append(LocatorStrategy(type="text", value=name))
    if not locators:
        locators.append(LocatorStrategy(type="text", value=name or "unknown"))
    return locators
