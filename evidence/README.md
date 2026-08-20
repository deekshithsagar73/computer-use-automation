# Evidence

Run artifacts for the interface.ai computer-use assignment. Each run directory contains:

| File | Purpose |
|---|---|
| `events.jsonl` | Timestamped act / decide / escalate / resume log |
| `result.json` | Final status, `outcome_code`, outputs, `llm_calls` |
| `final.png` / `outcome.png` | Page screenshot at success or business outcome |
| `intervention.json` | HITL pause: step, reason, operator instructions |
| `intervention.png` | Page at handoff |

## Canonical folders (submission)

| Path | Scenario |
|---|---|
| `discovery/20260816T234846Z/` | OpenAI discovery, 5 LLM calls, compiled capability |
| `replay-success/lookup-balance-demo/` | Headed replay, account activity, `llm_calls=0` |
| `replay-success/find-transactions-demo/` | Complex date-range search, `llm_calls=0` |
| `replay-error/invalid-login-demo/` | Empty login → `invalid_login` + `outcome.png` |
| `hitl/handoff-success/` | Pause on overview, human completes account open, automation finishes |
| `hitl/live-handoff/` | *(optional)* same flow with a real operator click during interview |

## HITL evidence notes

`handoff-success` used `CUA_SIMULATE_HUMAN=1` so the run completes in automation without an operator present. For a live demo, run the same capability **without** that variable, click the account in the headed window, then `cli.py resume --run-id <folder>`.
