# cua — computer-use agent: discover → typed artifact → deterministic replay

A vertical slice of the pattern in the brief: an LLM **discovers** how to drive
a legacy, no-API back-office banking UI to accomplish a goal; the run is
recorded as a **typed, versioned, replayable artifact**; that artifact is then
**replayed deterministically without the LLM**; when replay hits a state it
can't safely resolve it **escalates to a human on the same live session**; and
**safety guardrails** (allowlist, risky-action gating, PII/secret redaction)
apply throughout.

The target app is a bundled mock — `CoreServ 7.2` — a deliberately legacy,
server-rendered, table-based UI with **no test IDs** and **injectable failure
states**, so every branch of the error taxonomy can be demonstrated on demand.

## Runs without any live services

There is **nothing external to stand up**: no real bank, and no LLM key needed.

* The bank is a bundled Flask app started in-process by the CLI.
* Discovery uses `MockPlanner`, a deterministic, offline planner that encodes
  the domain knowledge an LLM would discover **and emits the exact same typed
  `StepSpec` output contract** a real model would. It still reacts to live
  perception step by step. A real `ClaudePlanner` (Anthropic tool-use) is wired
  and used automatically with `--planner claude` if `ANTHROPIC_API_KEY` is set —
  replay is identical either way, because replay never calls a planner.

## Setup

Requires **Python 3.8+** (tested on 3.8 and 3.12) and **Node 18+** for the
frontend.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # flask pydantic requests beautifulsoup4 lxml
export PYTHONPATH=src
```

## Interactive frontend ("Bridge")

A React + Vite operator console lives in `frontend/`. It talks to a small
Flask JSON API (`src/cua/api.py`) that wraps the exact same discover/replay
pipeline the CLI uses — no new execution logic, just an HTTP adapter, so
everything documented below in "Demo path" is also drivable by clicking
through the UI.

Run the two halves in separate terminals:

```bash
# terminal 1 — API + mock bank (persistent for the session)
export PYTHONPATH=src
python -m cua.api                     # http://127.0.0.1:5050

# terminal 2 — frontend
cd frontend
npm install
npm run dev                           # http://127.0.0.1:5173
```

Open `http://127.0.0.1:5173`. Pick a scenario and a case from the queue, run
discovery, approve the artifact, then run replay. Toggle "Operator on
standby" before replaying member `77777` to see the escalation handoff play
out live in the event stream. A "Reset demo data" button clears artifacts and
run history for a clean slate.

The Vite dev server proxies `/api/*` to `127.0.0.1:5050` (see
`frontend/vite.config.js`), so there's no CORS configuration needed. The API
also sets permissive CORS headers directly, in case you run the built
frontend (`npm run build && npm run preview`) from a different origin.

Because the API keeps the mock bank running for the whole session (unlike the
CLI, which starts/stops it per command), each `/api/discover` and
`/api/replay` call resets the injected transient-failure counters first, so
scenarios like member `60503`'s one-time 503 stay repeatable across many UI
clicks rather than only working once.

## One-command CLI demo

```bash
bash scripts/run_demo.sh
```

This regenerates the artifacts and writes fresh evidence under `evidence/` for
every scenario below.

## Demo path (individual CLI commands)

```bash
export PYTHONPATH=src

# 1) Discover a capability from a natural-language goal, save the artifact.
python -m cua.cli discover --preset balance --member-id 12345
python -m cua.cli show     --cap read_savings_balance         # inspect the artifact
python -m cua.cli approve  --cap read_savings_balance         # gate for unattended replay

# 2) Deterministic replay (no LLM). Three result classes:
python -m cua.cli replay --preset balance --member-id 12345   # SUCCESS  -> returns balance
python -m cua.cli replay --preset balance --member-id 00000   # BUSINESS_OUTCOME: member_not_found
python -m cua.cli replay --preset balance --member-id 99999   # BUSINESS_OUTCOME: permission_denied

# 3) Recoverable conditions handled automatically:
python -m cua.cli replay --preset balance --member-id 55555   # dismisses a privacy interstitial -> SUCCESS
python -m cua.cli replay --preset balance --member-id 60503   # retries a transient 503        -> SUCCESS

# 4) Escalation on an unexpected state (no matching outcome, checkpoint fails):
python -m cua.cli replay --preset balance --member-id 77777              # FAILURE (typed + evidence)
python -m cua.cli replay --preset balance --member-id 77777 --operator   # human clears it on the SAME session -> SUCCESS

# 5) Safety: risky/irreversible step is blocked unless explicitly confirmed:
python -m cua.cli replay --preset subaccount --member-id 12345 --account-type Savings                   # FAILURE: risky step needs confirmation
python -m cua.cli replay --preset subaccount --member-id 12345 --account-type Savings --confirm-risky    # SUCCESS: returns reference
```

## What to look at

* `src/cua/artifact/schema.py` — the capability artifact (typed params/outputs,
  semantic locators + fallbacks, checkpoints, and a **declarative error
  taxonomy**).
* `src/cua/replay/engine.py` — the deterministic replay engine and the tri-state
  result contract (`SUCCESS` / `BUSINESS_OUTCOME` / `FAILURE`).
* `src/cua/surface/base.py` — the `Surface` seam (perceive/act) that makes the
  artifact and replay engine surface-agnostic (HTTP/HTML today, Playwright/a11y
  designed).
* `src/cua/escalation/handoff.py` — the control-transfer model over one live
  session.
* `src/cua/api.py` — the JSON API the frontend drives (thin adapter, no new
  logic).
* `frontend/` — the "Bridge" operator console (React + Vite).
* `evidence/` — redacted JSONL logs + snapshots from real runs.
* `REPORT.md` — design writeup.

## Layout

```
src/cua/
  mockbank/     the legacy target app + in-process server
  surface/      Surface seam: HttpHtmlSurface (primary) + Playwright stub
  agent/        discovery loop, planner seam (mock + Claude), templating
  artifact/     schema, recorder, store
  replay/       deterministic replay engine + error-taxonomy interpreter
  safety/       allowlist policy + redaction
  escalation/   human-in-the-loop handoff / control transfer
  observability/redacting structured logger
  api.py        Flask JSON API for the frontend
  cli.py        discover | replay | list | show | approve
frontend/       React + Vite operator console ("Bridge")
```

## Tests / verification

The demo script is the acceptance test: it exercises success, both business
outcomes, both recoverable classes, a hard failure with typed diagnostics, the
same-session human handoff, and the risky-action gate. Sensitive values
(password, balances) are verified absent from all on-disk logs.
