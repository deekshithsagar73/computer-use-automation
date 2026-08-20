# Computer-use capability runtime

Record-once / replay-many layer for legacy web UIs without an API. OpenAI discovers a flow once and compiles a typed **capability** JSON file. **Replay** runs that file in Playwright with **`llm_calls=0`**.

Target: [ParaBank](https://parabank.parasoft.com/parabank/index.htm) (demo credentials `john` / `demo` in `.env.example`).

---

## Reviewer quickstart (no API key needed)

```bash
git clone https://github.com/deekshithsagar73/computer-use-automation.git
cd computer-use-automation
python -m venv .venv && .venv\Scripts\activate    # Windows
pip install -e ".[dev]"
playwright install chromium

# 1) Deterministic replay — balance lookup
python cli.py replay --capability capabilities/lookup_balance.v1.json --params capabilities/params.success.json --evidence evidence/replay-success/reviewer-run

# 2) Business error — invalid login (not a crash)
python cli.py replay --capability capabilities/lookup_balance.v1.json --params capabilities/params.invalid_login.json --evidence evidence/replay-error/reviewer-run

# 3) Complex flow — find transactions by date range
python cli.py replay --capability capabilities/find_transactions.v1.json --params capabilities/params.find_transactions.json --evidence evidence/replay-success/reviewer-find-tx
```

Confirm `llm_calls: 0` in the printed JSON. Pre-recorded evidence (logs + screenshots) is under **`evidence/`** — index in [`evidence/README.md`](evidence/README.md).

Design write-up: [`REPORT.md`](REPORT.md).

---

## What is in the submission

| Deliverable | Location |
|---|---|
| Capability schema + runtime | `src/capability_runtime/` |
| Hand-written capabilities | `capabilities/*.v1.json` |
| OpenAI discovery artifact | `capabilities/lookup_balance.generated.json` |
| Discovery evidence | `evidence/discovery/20260816T234846Z/` |
| Replay success + PNG | `evidence/replay-success/lookup-balance-demo/` |
| Complex workflow | `evidence/replay-success/find-transactions-demo/` |
| Invalid login + PNG | `evidence/replay-error/invalid-login-demo/` |
| **Live human handoff** | `evidence/hitl/live-handoff/` |
| Unit tests | `tests/` (`pytest -q`) |

---

## Architecture

| Component | Role |
|---|---|
| `DiscoveryRun` | LLM + accessibility snapshot → compiler → `capability.v1` |
| `ReplayEngine` | Policy + locator ladder + outcomes; never calls an LLM |
| `LiveSession` | `automation` ↔ `human` control for HITL |
| `Policy` | Host/action allowlist, irreversible click blocks, redaction |
| `Surface` | Playwright observe/act (seam for other drivers) |

Locators are DOM-based (`name`, `id`, `role`, CSS, XPath ladder) — not screenshots or pixel coordinates.

---

## Setup (full)

Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium
copy .env.example .env
```

`OPENAI_API_KEY` in `.env` is required **only** for `discover`. Replay does not need a model key.

---

## Run commands

Chromium opens **headed** (visible) unless `--headless` is passed.

### Replay — lookup balance

```bash
python cli.py replay --capability capabilities/lookup_balance.v1.json --params capabilities/params.success.json --evidence evidence/replay-success/lookup-balance-demo
```

### Replay — invalid login

```bash
python cli.py replay --capability capabilities/lookup_balance.v1.json --params capabilities/params.invalid_login.json --evidence evidence/replay-error/invalid-login-demo
```

### Replay — find transactions (complex)

```bash
python cli.py replay --capability capabilities/find_transactions.v1.json --params capabilities/params.find_transactions.json --evidence evidence/replay-success/find-transactions-demo
```

### Discovery (OpenAI)

```bash
python cli.py discover --goal "Log in as john, open the first account, read the available balance, stop on the account activity page."
python cli.py replay --capability capabilities/lookup_balance.generated.json --params capabilities/params.success.json
```

### Human-in-the-loop (operator clicks in Chromium)

**Terminal A** — automation pauses on Accounts Overview:

```bash
python cli.py replay --capability capabilities/lookup_balance.hitl-demo.json --params capabilities/params.success.json --hitl --evidence evidence/hitl/live-handoff
```

Click the **account number link** in the browser window.

**Terminal B** — hand control back:

```bash
python cli.py resume --run-id live-handoff
```

Recorded proof with a real operator click: **`evidence/hitl/live-handoff/`** (`human_actions` in `events.jsonl`, no `simulated_human` event).

The folder **`evidence/hitl/handoff-simulated/`** (if present) is the same flow completed by a dev-only env flag for automated runs — not a substitute for live handoff.

---

## Tests

```bash
pytest -q
```

---

## UI drift and errors

- Each step stores an ordered **locator ladder**; replay tries `name` → `id` → `role` → CSS → XPath.
- Expected domain failures map to **`business_outcome`** (e.g. `invalid_login`), not a stack trace.
- Locator exhaustion → **`failed`** + screenshot, or **HITL pause** with `--hitl`.
- Replay does **not** call an LLM to heal missed locators; update the capability or re-run discovery.

---

## Safety

- Allowlist: `parabank.parasoft.com`
- Blocked: Transfer Funds, Bill Pay, Open New Account
- Secrets: `${password}` in artifacts; redacted in logs
