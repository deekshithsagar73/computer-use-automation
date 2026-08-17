# Evidence

This folder holds run artifacts required by the assignment. Each run is a directory with `events.jsonl`, `result.json`, and a PNG of the page.

| Subfolder | What belongs here | Canonical run |
|---|---|---|
| `discovery/` | OpenAI observe/decide/act log and generated capability | `20260816T234846Z` (`llm_calls=5`) |
| `replay-success/` | Deterministic replay, `llm_calls=0`, activity page PNG | `20260817T000321Z` (`final.png`) |
| `replay-error/` | Empty login → `invalid_login`, not a crash | `20260817T000448Z` (`outcome.png`) |
| `hitl/` | Locator miss, pause, `intervention.json`, resume | `locator-miss/` |

## How to read a run

- `result.json` — status, outcome_code, outputs, `llm_calls`
- `events.jsonl` — timestamped act/decide/escalated/resumed lines
- `final.png` / `outcome.png` / `intervention.png` — page at the end or at handoff
- `intervention.json` — why automation stopped and how to resume (`cli.py resume --run-id …`)
