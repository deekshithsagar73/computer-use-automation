"""CLI: discover, replay, resume. Replay never calls an LLM."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from capability_runtime.discovery.loop import DiscoveryRun
from capability_runtime.policy.engine import Policy
from capability_runtime.replay.engine import ReplayEngine
from capability_runtime.schema.capability import Capability

ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    load_dotenv(ROOT / ".env", override=True)


def _policy() -> Policy:
    hosts = os.getenv("ALLOWED_HOSTS", "parabank.parasoft.com")
    return Policy(allowed_hosts=[h.strip() for h in hosts.split(",") if h.strip()])


def _params_from_args(args: argparse.Namespace) -> dict:
    if getattr(args, "params", None):
        return json.loads(Path(args.params).read_text(encoding="utf-8"))
    return {
        "username": os.getenv("PARABANK_USERNAME", "john"),
        "password": os.getenv("PARABANK_PASSWORD", "demo"),
    }


def _run_dir(kind: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ROOT / "evidence" / kind / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def cmd_discover(args: argparse.Namespace) -> int:
    _load_env()
    if getattr(args, "provider", None):
        os.environ["LLM_PROVIDER"] = args.provider
    goal = args.goal
    url = args.url or os.getenv("TARGET_URL", "https://parabank.parasoft.com/parabank/index.htm")
    evidence = Path(args.evidence) if args.evidence else _run_dir("discovery")
    params = _params_from_args(args)
    headed = _resolve_headed(args)

    async def _go() -> int:
        run = DiscoveryRun(
            goal=goal,
            start_url=url,
            params=params,
            policy=_policy(),
            evidence_dir=evidence,
            headed=headed,
            max_steps=args.max_steps,
            hitl=args.hitl,
        )
        result, cap = await run.run()
        print(result.model_dump_json(indent=2))
        if cap:
            out_name = args.capability_out or "lookup_balance.generated.json"
            out = ROOT / "capabilities" / out_name
            cap.save(out)
            print(f"wrote {out}")
        print(f"evidence: {evidence}")
        return 0 if result.status.value in {"success", "business_outcome"} else 1

    return asyncio.run(_go())


def _resolve_headed(args: argparse.Namespace) -> bool:
    """HITL requires a visible browser so an operator can click the live page."""
    if getattr(args, "hitl", False):
        if getattr(args, "headless", False):
            print("HITL mode opens a headed Chromium window (--headless ignored).", file=sys.stderr)
        return True
    return not args.headless


def cmd_replay(args: argparse.Namespace) -> int:
    _load_env()
    cap = Capability.load(args.capability)
    params = _params_from_args(args)
    evidence = Path(args.evidence) if args.evidence else _run_dir(args.evidence_kind)
    headed = _resolve_headed(args)

    async def _go() -> int:
        engine = ReplayEngine(
            cap,
            params,
            _policy(),
            hitl=args.hitl,
            headed=headed,
            evidence_dir=evidence,
        )
        result = await engine.run()
        print(result.model_dump_json(indent=2))
        print(f"llm_calls={result.llm_calls} evidence={evidence}")
        return 0 if result.status.value in {"success", "business_outcome", "recovered"} else 1

    return asyncio.run(_go())


def cmd_resume(args: argparse.Namespace) -> int:
    evidence = ROOT / "evidence"
    matches = list(evidence.glob(f"**/{args.run_id}"))
    if not matches:
        # allow passing a full path or the stamp folder name
        direct = Path(args.run_id)
        if direct.exists():
            matches = [direct]
        else:
            matches = [p for p in evidence.rglob("intervention.json") if p.parent.name == args.run_id]
            matches = [p.parent for p in matches]
    if not matches:
        print(f"no run found for {args.run_id}", file=sys.stderr)
        return 1
    target = matches[0]
    (target / "resume.signal").write_text("resume", encoding="utf-8")
    print(f"signaled resume for {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Computer-use capability runtime")
    sub = parser.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="LLM-driven run; writes a capability on success")
    d.add_argument("--goal", required=True)
    d.add_argument("--url")
    d.add_argument("--params")
    d.add_argument("--evidence")
    d.add_argument("--max-steps", type=int, default=25)
    d.add_argument("--headless", action="store_true", help="Headless browser (default: headed for demos)")
    d.add_argument("--hitl", action="store_true")
    d.add_argument("--capability-out", help="Filename under capabilities/ for a successful compile")
    d.add_argument("--provider", choices=["openai", "gemini", "scripted"], help="Override LLM_PROVIDER")
    d.set_defaults(func=cmd_discover)

    r = sub.add_parser("replay", help="Deterministic replay; llm_calls=0")
    r.add_argument("--capability", required=True)
    r.add_argument("--params")
    r.add_argument("--evidence")
    r.add_argument("--evidence-kind", default="replay-success")
    r.add_argument("--headless", action="store_true", help="Headless browser (default: headed for demos)")
    r.add_argument("--hitl", action="store_true")
    r.set_defaults(func=cmd_replay)

    s = sub.add_parser("resume", help="Hand control back after HITL pause")
    s.add_argument("--run-id", required=True)
    s.set_defaults(func=cmd_resume)
    return parser


def main(argv: list[str] | None = None) -> int:
    _load_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
