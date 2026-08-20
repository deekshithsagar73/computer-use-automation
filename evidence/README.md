# Evidence index

Pre-recorded runs for the interface.ai computer-use take-home. Each folder has `events.jsonl`, `result.json`, and PNGs where noted.

## Submission checklist

| Folder | Proves | Key files |
|---|---|---|
| `discovery/20260816T234846Z/` | OpenAI discovery, 5 LLM calls | `events.jsonl`, `capability.json`, `result.json` |
| `replay-success/lookup-balance-demo/` | Deterministic replay, balance read | `final.png`, `llm_calls: 0` |
| `replay-success/find-transactions-demo/` | Multi-step form + `select` step | `final.png`, `llm_calls: 0` |
| `replay-error/invalid-login-demo/` | `invalid_login` business outcome | `outcome.png`, `llm_calls: 0` |
| **`hitl/live-handoff/`** | **Real operator handoff** | `intervention.json`, `human_actions` in log |

## Human-in-the-loop — which folder to trust

| Folder | Operator |
|---|---|
| **`live-handoff/`** | **Real.** Operator clicked the account link in Chromium; `events.jsonl` contains `human_actions` and no `simulated_human` line. |
| `handoff-simulated/` | Automated only. Run used `CUA_SIMULATE_HUMAN=1` to navigate programmatically; documents the resume code path without a person present. |

To reproduce live handoff: README § Human-in-the-loop.

## File glossary

| File | Meaning |
|---|---|
| `events.jsonl` | Timestamped log: act, escalate, resume, screenshot |
| `result.json` | Final status, outputs, `llm_calls` |
| `intervention.json` | Why automation paused + resume command |
| `intervention.png` | Page at pause |
| `final.png` / `outcome.png` | Page at success or business error |
