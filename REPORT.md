# REPORT

## 1. Architecture

This system is a record-once / replay-many integration layer for legacy UIs without APIs—not a general-purpose browser agent.

Two runtimes share one Playwright session and one policy engine:

- **DiscoveryRun** calls OpenAI (`gpt-4.1-mini`) with a compact accessibility snapshot and a fixed JSON action schema (`click`, `type`, `select`, `navigate`, `extract`, `done`). The model runs only here.
- **ReplayEngine** interprets a saved `capability.v1` file. It never imports an LLM client; `llm_calls` is always zero.

The deliverable is the capability file. Discovery transcripts live under `evidence/` for debugging; the compiler emits a separate contract under `capabilities/`.

The shape follows [workflow-use](https://github.com/browser-use/workflow-use) (generate → parameterized workflow → run without AI) and the control loop of [browser-use](https://github.com/browser-use/browser-use) (numbered controls, structured actions, allowlist, secret placeholders, pause with browser left open). Neither library is wrapped.

**Control plane:** accessibility-style DOM snapshots (role, name, id, input value)—not screenshots. Screenshots are evidence on failure or at checkpoint only. The `Surface` interface is the seam for a future desktop adapter.

**Process model:** one CLI process, one Chromium. No queues or cloud browsers in v1.

## 2. Artifact schema

`capability.v1` (`schema/capability.py`) defines:

- `app` (`vendor`, `product`, `entry_url`) for multi-tenant identity
- typed `params` / `outputs`
- ordered `steps`: `navigate` | `click` | `type` | `select` | `extract` | `assert`
- locator ladder per control (never a live snapshot index)
- `checkpoint` and `outcomes` (business results vs crashes)
- per-step `risk`: `safe` | `confirm` | `blocked`

Secrets are `${password}` references; the schema rejects embedded password literals.

**Shipped capabilities:**

| ID | Steps | Notes |
|---|---|---|
| `parabank.lookup_balance` | 8 | Login → overview → activity → balance |
| `parabank.find_transactions` | 11 | Login → find form → date range → row count |
| `parabank.lookup_balance.hitl_demo` | 8 | Stale locator on account click for operator handoff |

## 3. Determinism and error handling

Replay walks steps, substitutes params, enforces policy, resolves locators with Playwright waits, evaluates `outcomes` after each step.

| Status | Meaning |
|---|---|
| `success` | Checkpoint satisfied |
| `business_outcome` | Expected domain failure (e.g. `invalid_login`) |
| `recovered` | One timeout retry, or human completed a blocked step |
| `escalated` | HITL timeout or human resumed without satisfying step |
| `failed` | Locator miss, policy block, or checkpoint miss |

UI drift is handled by the locator ladder, not silent LLM repair at replay time.

## 4. Heterogeneity and multi-tenant

Capabilities name *what* to do; `Surface` knows *how* to act on Playwright. A new site requires a new discovery run, a new capability file, and an allowlist entry—not a rewrite of the runtime.

`app.vendor` + `app.product` identify reusable flows. Tenant overlays (same step graph, different locators) are designed in but not implemented in v1.

## 5. Escalation and handoff

Stuck conditions: locator miss, policy block, action loop, max steps.

On HITL pause:

1. `controller` becomes `human`
2. `intervention.json` records step, reason, URL, operator instructions, screenshot
3. Chromium stays open (headed mode; `--headless` is ignored when `--hitl` is set)
4. Operator completes the step in the browser window
5. `cli.py resume --run-id …` writes `resume.signal`
6. Replay retries the step; if the operator already navigated to the target page, the step is **skipped** and remaining steps continue

Outcome after a successful handoff: `success_after_human` with a `recovered_events` entry.

Evidence: `evidence/hitl/handoff-success/` (simulated operator for automation); live interview demo uses the same flow without `CUA_SIMULATE_HUMAN`.

## 6. Safety

Policy is code:

- host allowlist (`parabank.parasoft.com`)
- action allowlist (includes `select`; no JS eval, upload, download)
- irreversible UI names blocked (Transfer, Bill Pay, Open New Account)
- secret substitution at act time; JSONL redaction

## 7. Scope and extensions

**In v1:** discovery, replay, two ParaBank capabilities (lookup + find transactions), HITL control transfer, evidence folders, 20 unit tests.

**Explicitly out of v1:**

- LLM fallback on replay failure
- Operator web UI (headed browser + JSON is the console)
- Desktop driver implementation
- Task queue / multi-tenant storage
- Second target application

**Natural next steps:** minimal operator UI (stream + Resume button), bounded one-step heal behind policy, tenant overlay files, stability scoring over N replays.
