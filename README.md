# Computer-use capability runtime

Integration layer for legacy web UIs that have no API. A model discovers a flow once; the run compiles into a typed **capability** JSON file. Production **replay** executes that file with Playwright and **never calls an LLM**.

Target application: [ParaBank](https://parabank.parasoft.com/parabank/index.htm) (Parasoft demo bank). All interaction is through the UI; ParaBank REST endpoints are not used.

## Architecture (short)

| Runtime | Role |
|---|---|
| `DiscoveryRun` | LLM + accessibility snapshot → action loop → compiler emits `capability.v1` |
| `ReplayEngine` | Walks capability steps, policy checks, locator ladder, `llm_calls=0` |
| `LiveSession` | Single Chromium context; `automation` ↔ `human` controller for HITL |
| `Policy` | Host allowlist, action allowlist, irreversible click blocks, secret redaction |
| `Surface` | Playwright observe/act (extensible to other drivers via the same interface) |

Design detail: `REPORT.md`.

## Setup

Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
playwright install chromium
copy .env.example .env
```

`.env` holds `OPENAI_API_KEY` for **discovery only**. Replay does not need a model key.

## Capabilities shipped in this repo

| File | Flow |
|---|---|
| `lookup_balance.v1.json` | Login → first account → available balance |
| `find_transactions.v1.json` | Login → Find Transactions → date-range search → row count |
| `lookup_balance.hitl-demo.json` | Same as lookup balance; stale locator on account click to demo HITL |
| `lookup_balance.generated.json` | Produced by the last successful OpenAI discovery run |

## Demo commands (headed browser by default)

Chromium opens visibly unless `--headless` is passed.

### Simple replay (no model)

```bash
python cli.py replay --capability capabilities/lookup_balance.v1.json --params capabilities/params.success.json --evidence evidence/replay-success/lookup-balance-demo
```

Business outcome (empty login → `invalid_login`, not a crash):

```bash
python cli.py replay --capability capabilities/lookup_balance.v1.json --params capabilities/params.invalid_login.json --evidence evidence/replay-error/invalid-login-demo
```

### Complex replay (multi-step form, `select` step)

```bash
python cli.py replay --capability capabilities/find_transactions.v1.json --params capabilities/params.find_transactions.json --evidence evidence/replay-success/find-transactions-demo
```

### Discovery (OpenAI)

```bash
python cli.py discover --goal "Log in as john, open the first account, read the available balance, stop on the account activity page."
python cli.py replay --capability capabilities/lookup_balance.generated.json --params capabilities/params.success.json --evidence evidence/replay-success/generated-replay
```

### Human-in-the-loop (live interview demo)

Terminal A — replay pauses on Accounts Overview; **click the account link in the Chromium window**:

```bash
python cli.py replay --capability capabilities/lookup_balance.hitl-demo.json --params capabilities/params.success.json --hitl --evidence evidence/hitl/live-handoff
```

Terminal B — after clicking the account and reaching the activity page:

```bash
python cli.py resume --run-id live-handoff
```

Automation resumes, extracts balance, finishes with `success_after_human` and `llm_calls=0`.

Evidence under `evidence/hitl/handoff-success/` was captured with `CUA_SIMULATE_HUMAN=1` for CI; live demos should omit that variable.

## Tests

```bash
pytest -q
```

## Repository layout

```
src/capability_runtime/
  schema/       capability.v1 + result taxonomy
  policy/       allowlist, redaction
  surface/      Playwright adapter
  replay/       deterministic interpreter
  discovery/    LLM loop + compiler
  session/      HITL control transfer
  llm/          OpenAI / Gemini clients
capabilities/   versioned workflow artifacts
evidence/       run logs, PNGs, intervention.json
REPORT.md       design write-up (assignment headings)
```

## Safety defaults

- Host allowlist: `parabank.parasoft.com`
- Blocked clicks: Transfer Funds, Bill Pay, Open New Account
- Passwords stored as `${password}` in artifacts; values redacted in JSONL
