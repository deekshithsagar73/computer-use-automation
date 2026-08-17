# Computer-use capability runtime

Backend integration layer that gives an AI agent hands on a legacy UI with **no API**.

The model discovers a flow once. The successful run is saved as a typed, versioned **capability**. Production replay executes that capability **without an LLM**.

Target surface: [ParaBank](https://parabank.parasoft.com/parabank/index.htm) (Parasoft’s public demo bank). We drive the UI only; we never call ParaBank’s REST API.

## Setup

Python 3.11+. From this directory:

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -e ".[dev]"
playwright install chromium
copy .env.example .env   # Windows
# cp .env.example .env   # macOS/Linux
```

Edit `.env`:

- `OPENAI_API_KEY` — required **only** for `discover`. Replay does not need a model key.
- Optional: `LLM_PROVIDER=gemini` plus `GOOGLE_API_KEY` if you want the Gemini fallback.

Default discovery model: `gpt-4.1-mini`.

## Demo path (exact commands)

### 1. Replay without a model (no API key)

Happy path:

```bash
python cli.py replay --capability capabilities/lookup_balance.v1.json --params capabilities/params.success.json --headless --evidence-kind replay-success
```

Expected business outcome (bad password — not a crash):

```bash
python cli.py replay --capability capabilities/lookup_balance.v1.json --params capabilities/params.invalid_login.json --headless --evidence-kind replay-error
```

`llm_calls` in the printed result must be `0`.

### 2. Discover with OpenAI, then replay the generated artifact

```bash
python cli.py discover --goal "Log in as john, open the first account, read the available balance, stop on the account activity page." --headless
python cli.py replay --capability capabilities/lookup_balance.generated.json --params capabilities/params.success.json --headless --evidence-kind replay-success
```

`discover` uses `LLM_PROVIDER` from `.env` (default `openai`). Override with `--provider gemini` if a Google key is set.

### 3. Human-in-the-loop

In terminal A (headed browser stays open when stuck):

```bash
python cli.py replay --capability capabilities/lookup_balance.v1.json --params capabilities/params.success.json --hitl
```

If automation pauses, `evidence/<kind>/<run>/intervention.json` explains why. Use the same live window, then:

```bash
python cli.py resume --run-id <run-folder-name>
```

Recorded HITL evidence (locator miss after login, pause, resume):

```bash
python cli.py replay --capability capabilities/lookup_balance.hitl-miss.json --params capabilities/params.success.json --hitl --headless --evidence evidence/hitl/locator-miss
python cli.py resume --run-id locator-miss
```

### Week-1 locator gate (no LLM)

```bash
python scripts/parabank_login.py
```

## Tests (no live LLM)

```bash
pytest -q
```

## Layout

| Path | Role |
|---|---|
| `src/capability_runtime/schema/` | Capability v1 + result taxonomy |
| `src/capability_runtime/policy/` | Host/action allowlist, redaction |
| `src/capability_runtime/surface/` | Playwright observe/act |
| `src/capability_runtime/replay/` | Deterministic interpreter |
| `src/capability_runtime/discovery/` | OpenAI loop + compiler |
| `src/capability_runtime/session/` | `automation` / `human` control |
| `capabilities/` | Reviewable artifacts |
| `evidence/` | Discovery + replay logs |
| `REPORT.md` | Design write-up |

## Safety

- Allowlist: `parabank.parasoft.com` only.
- Secrets are `${password}` refs in artifacts; logs redact values.
- Transfer / Bill Pay / Open New Account clicks are blocked as irreversible.
- Demo user `john` / `demo` is public test data, not real PII.
