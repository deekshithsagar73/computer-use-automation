# REPORT

## 1. Architecture

This system is a thin record-once / replay-many layer, not a general browser agent. Two runtimes share one Playwright session and one policy engine:

- **DiscoveryRuntime** calls OpenAI (`gpt-4.1-mini` by default) with a compact accessibility snapshot and a tiny JSON action schema (`click`, `type`, `navigate`, `extract`, `done`). That is the only place a model runs. Gemini remains an env-var swap (`LLM_PROVIDER=gemini`).
- **ReplayRuntime** interprets a saved `capability.v1` JSON file. It never imports an LLM client. `llm_calls` is always 0.

The product is the capability file, not the chat. Discovery writes a transcript under `evidence/` for debugging; the compiler emits a separate, reviewable contract under `capabilities/`.

We followed the **shape** of [workflow-use](https://github.com/browser-use/workflow-use) (generate → parameterized workflow → run with no AI) and the **loop mechanics** of [browser-use](https://github.com/browser-use/browser-use) (observe numbered controls, structured actions, domain allowlist, secret placeholders, pause that leaves the browser open). We did not wrap either library. Reviewers can read every line.

Trade-off: Playwright + accessibility-ish DOM extraction instead of screenshot+coordinates (Anthropic/OpenAI CUA) or raw CDP (browser-use). Reason: ParaBank is a server-rendered JSP app; role/name/id locators are stable; screenshots are expensive, trip Gemini safety filters, and make the artifact unreviewable. The `Surface` type is the seam for a later desktop adapter.

Process model: one CLI process, one Chromium. No queues. The brief asked us not to build scaling infrastructure.

## 2. Artifact schema

`capability.v1` is a Pydantic model (`schema/capability.py`) with:

- identity and `app` (`vendor`, `product`, `entry_url`) so a later tenant overlay can specialize the same vendor product
- typed `params` / `outputs`
- ordered `steps` (`navigate` | `click` | `type` | `extract` | `assert`)
- a **locator ladder** per control, never a live snapshot index
- `checkpoint` (URL regex + required outputs)
- `outcomes` that name business results (`invalid_login`) separately from crashes
- `risk` per step (`safe` | `confirm` | `blocked`)

Password is a `${password}` reference. The model rejects artifacts that look like they embedded `password: ...` literals.

We did **not** copy workflow-use’s rule that every workflow must end with an LLM `extract`. Extraction is a Playwright text read so replay stays model-free.

Locator order in the compiler: role+name, placeholder, name, id, text, then CSS/XPath as last resort. The hand-written ParaBank capability puts `name=` first on form fields because that is what this JSP actually exposes.

## 3. Determinism and error handling

Replay walks steps, substitutes params, enforces policy, resolves the locator ladder with Playwright waits (not `sleep`), then evaluates `outcomes` after every step.

Taxonomy:

- **success** — checkpoint holds; outputs filled
- **business_outcome** — e.g. “could not be verified” after login; the caller needs this
- **recovered** — one timeout retry then success
- **escalated** — HITL pause; human used the same session
- **failed** — locator miss, policy, or checkpoint miss, with step id, expected, observed, screenshot

UI drift is secondary on this surface. The ladder (name → CSS → XPath) is the drift hedge. We do not silently LLM-heal a miss; that is a Section 8 stretch and would put the model back in production.

## 4. Heterogeneity and multi-tenant

**Surface seam:** a capability names *what* to do and *how to identify a control* (`Target.locators`). `Surface.observe/act` is the only module that knows Playwright. A desktop adapter would implement the same two methods on UI Automation / AX trees; the JSON would grow a locator type, not a new runtime.

**Tenants:** `app.vendor` + `app.product` is the reusable identity (“Parasoft ParaBank / lookup balance”). A tenant overlay would be a sibling file that replaces locator values and `entry_url` without re-recording the step graph. Canonicalization: `${entry_url}`, `${username}`, account ids extracted at runtime rather than hardcoded. Drift detection: replay failure at a step plus the stored ladder vs current observe() names; that is an ops signal, not a silent rewrite.

We did not implement overlay storage. The fields are there so we are not painted into a per-tenant rebuild.

## 5. Escalation and handoff

Stuck = locator miss, policy block, action loop (same hash 4 times), or max steps.

Pause sets `controller=human`, writes `intervention.json` (capability, goal, step, why, URL, screenshot), and **does not close Chromium**. The operator uses the headed window. `python cli.py resume --run-id …` writes `resume.signal`. After resume, replay retries the failed step once, then continues remaining steps or marks `escalated`. DOM clicks while `controller=human` are logged as `actor: human`.

Live evidence: `evidence/hitl/locator-miss/` (overview page screenshot, `intervention.json`, resume, `human_resumed`).

There is no co-browse console. The PDF allows a mock operator surface; the control-transfer model is real.

## 6. Safety

Policy is code, not a prompt:

- host allowlist (`parabank.parasoft.com`)
- action allowlist (no JS eval, upload, download)
- irreversible name markers (Transfer, Bill Pay, Open New Account) → blocked
- secret substitution at act time; JSONL redacts values and long digit runs

Limits: allowlists are not a sandbox. A malicious page on an allowed host can still display phishing content. We do not persist credentials in artifacts. The model still sees the username in the discovery prompt; the password is typed by the runtime when the model targets the password field. OpenAI is the default because a live ParaBank discovery finished in ~31s / 5 calls versus ~106s / 5 calls on Gemini Flash, and this submission is budgeted on OpenAI.

## 7. Cuts

Left out on purpose:

- LLM fallback on a failed replay step (stretch; contradicts 3.3)
- Desktop driver (design only)
- Tenant overlay implementation
- Operator web UI
- Queues, cloud browsers, wrapping browser-use
- A second target app

Next: bounded one-step heal behind policy, tenant overlay files, and a stability score over N replays.
