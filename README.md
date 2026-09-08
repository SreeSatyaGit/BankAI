# BankAI

A minimal, real skeleton for a computer-use agent: paste a live target URL and a
goal in plain English, and a Python backend uses an LLM (Groq) to drive that page
step by step until the goal is met (or it honestly gives up), recording the
successful run as a typed, replayable **Capability** artifact.

**Status:** discovery AND deterministic replay are both implemented. Replay
runs a saved artifact's steps directly via Playwright with NO LLM involved —
see `backend/replay.py`.

There is no built-in target, no demo bank, no seeded data. You paste a real URL;
the agent drives whatever is actually there.

## How it works

```
target_url + goal
      │
      ▼
POST /api/discover  ──▶ returns {run_id} immediately, run continues in the background
      │
      ▼
 loop.py  ── perceive() ──▶  surface.py (Playwright, semantic locators)
   │  ▲                            │
   │  └──── act(step) ─────────────┘
   ▼
 planner.py (GroqPlanner — the ONLY file that talks to an LLM)
   │
   ├─ risky step, planner stuck, or a step exhausts its retries? ──▶ pause,
   │  surface via GET /api/discover/{run_id} as "awaiting_intervention" ──▶
   │  human can act directly on the SAME live session via
   │  POST /api/discover/{run_id}/manual-action, then resume via
   │  POST /api/discover/{run_id}/resume ──▶ continue, skip, retry, or abort
   ▼
 on success: Capability artifact ──▶ store.py ──▶ artifacts/*.json
```

- `backend/schema.py` — the typed contracts (Locator, Action, StepSpec, Perception, Capability). No logic.
- `backend/surface.py` — perceive()/act() over a live Playwright page, using semantic locators (label / role+name / placeholder / text / name), not CSS/xpath. `perceive()` also tells you *which* strategy will resolve each field, since legacy forms often have no real label at all.
- `backend/planner.py` — `GroqPlanner`, the only place the LLM lives. Tool-calling with `next_step` / `finish`.
- `backend/loop.py` — the discovery loop: perceive → planner.next_step → act, until done/max-steps or a human aborts. A step gets a bounded mechanical retry (temporal robustness) before anything escalates. Three situations pause and escalate to a human: a `risky=True` step, the planner getting stuck, or a step exhausting its retries. Assembles and persists the artifact, and writes a screenshot to `runtime_evidence/` after every step.
- `backend/replay.py` — the deterministic replay engine: runs a saved Capability's steps directly via `Surface.act()`, no LLM involved, no live-pause capability (a hard failure is returned in the response for the caller to act on afterward — see its docstring for why). Per-step retry (temporal robustness) + `Locator.fallbacks` (structural robustness, in `surface.py`); classifies the outcome as `success` / `business_outcome` / `blocked` / `failed`; risky steps require an explicit `confirm_risky=true` per call.
- `backend/runs.py` — in-memory registry of in-flight runs; the real pause/resume primitive (`RunState.request_intervention` / `resolve_intervention`) all three escalation triggers share, plus `live_surface`/`live_run_dir` — what lets a human act on the exact live session a paused run is using, not a fresh one.
- `backend/templating.py` — shared `{{param}}` substitution logic used by both `loop.py` and `replay.py`.
- `backend/evidence.py` — shared screenshot-capture helper used by both `loop.py` and `replay.py`.
- `backend/store.py` — flat-file JSON persistence for artifacts, plus the `runtime_evidence/` path used for screenshots.
- `backend/main.py` — FastAPI endpoints. `POST /api/discover` starts a run in the background and returns a `run_id` immediately (it can no longer block for the whole run, since an escalation might pause indefinitely waiting on you); `GET /api/discover/{run_id}` polls for progress; `POST /api/discover/{run_id}/manual-action` lets a human act directly on the live paused session; `POST /api/discover/{run_id}/resume` answers a pending escalation; `POST /api/replay` runs a saved artifact deterministically (this one IS a single blocking call — no LLM in the loop means no open-ended wait to justify the background-task treatment).

Raw values you don't want frozen into an artifact (a member id, an amount, anything
that looks like PII) are never written to disk: the planner references them as
`{{param_name}}` in `action.value` and supplies the literal separately via
`param_bindings`, which `loop.py` strips before persisting the `StepSpec`. Only
param *names* survive into `Capability.params`.

### Human-in-the-loop escalation

Three situations pause a discovery run and hand it to a human:

- **Risky steps** — the planner marks any step it judges submits, deletes,
  transfers, or otherwise takes an irreversible/high-impact action as
  `risky: true` (e.g. a final "Register"/"Submit"/"Confirm" click). Discovery
  pauses *before* executing it — the browser does nothing yet.
- **A stuck planner** — the planner giving up used to be an automatic,
  terminal failure. It's now recoverable: a human can act on the live session
  first (see below), then let the agent try again.
- **Retries exhausted** — a step gets up to 3 mechanical attempts (handles
  transient timing issues automatically, no human needed) before escalating.

When any of these fire, the run's status becomes `"awaiting_intervention"` and
`GET /api/discover/{run_id}` returns a `pending_intervention` payload: the
trigger, why, the current page state, recent history, a screenshot, and (for
risky/retry-exhausted) the specific step. From there you can:

1. **Act directly on the live session** via `POST /api/discover/{run_id}/manual-action`
   — submit any `Action` (click, type, navigate, extract...) and it runs
   against the *exact same* live Playwright page the agent was using, not a
   fresh one. Call this as many times as you want while paused.
2. **Resume** via `POST /api/discover/{run_id}/resume` with a `decision`:
   `approve`/`skip` (risky), `retry`/`skip` (retry-exhausted), or `continue`
   (stuck) — `abort` always ends the run. The planner sees a human's decision
   (and any manual actions taken) in its history and adapts from there.

## Setup

### Backend

```bash
cd bankai
python3 -m venv .venv
source .venv/bin/activate       
pip install -r requirements.txt
playwright install chromium

cp .env.example .env                       # then edit .env and set bank_api_interface (or GROQ_API_KEY)

uvicorn backend.main:app --reload --port 8000
BANKAI_HEADLESS=false uvicorn backend.main:app --reload --port 8000
```

### Frontend

```bash
cd bankai/frontend
npm install
npm run dev
```

The Vite dev server proxies `/api/*` to `http://127.0.0.1:8000`, so open the URL
Vite prints (typically `http://localhost:5173`) once both are running.

## Demo path

1. Start the backend and frontend as above.
2. In the browser UI: paste a real target URL (any publicly reachable page — a
   demo/sandbox site is a good choice) and type a goal, e.g.
   *"open the search page and search for 'wireless mouse'"*.
3. Click **Run agent**. The step-by-step events stream into the panel as the agent
   drives the live page; on success the resulting artifact JSON is shown and saved
   to `artifacts/<id>.json`.

Equivalent from the command line, once the backend is running and `GROQ_API_KEY`
is set:

```bash
curl -s -X POST http://127.0.0.1:8000/api/discover \
  -H 'Content-Type: application/json' \
  -d '{"target_url": "https://example.com", "goal": "describe what this page offers"}' | python3 -m json.tool
# -> {"run_id": "run_xxx", "status": "running"} — returns immediately

curl -s http://127.0.0.1:8000/api/discover/run_xxx | python3 -m json.tool
# poll this until "status" is "done" (or "awaiting_intervention" if a risky
# step, a stuck planner, or an exhausted retry needs your input — see above)

# Optional, only while status == "awaiting_intervention": act on the live
# session directly before deciding what to do.
curl -s -X POST http://127.0.0.1:8000/api/discover/run_xxx/manual-action \
  -H 'Content-Type: application/json' \
  -d '{"action": {"type": "click", "locator": {"strategy": "role", "role": "button", "value": "Close"}}, "description": "dismissing a popup"}'

curl -s -X POST http://127.0.0.1:8000/api/discover/run_xxx/resume \
  -H 'Content-Type: application/json' \
  -d '{"decision": "approve"}'
# "decision" is one of: approve | skip | continue | retry | abort
# (which ones make sense depends on the trigger — see the table above)
# only valid while status == "awaiting_intervention"

curl -s http://127.0.0.1:8000/api/artifacts | python3 -m json.tool
```

### Replay

```bash
curl -s -X POST http://127.0.0.1:8000/api/replay \
  -H 'Content-Type: application/json' \
  -d '{"artifact_id": "cap_xxx"}' \
  | python3 -m json.tool

# Override just the ones you care about — everything else still auto-fills.
curl -s -X POST http://127.0.0.1:8000/api/replay \
  -H 'Content-Type: application/json' \
  -d '{"artifact_id": "cap_xxx", "params": {"username": "john"}}' \
  | python3 -m json.tool

# Old strict behavior (fail fast if anything is missing) is still available:
curl -s -X POST http://127.0.0.1:8000/api/replay \
  -H 'Content-Type: application/json' \
  -d '{"artifact_id": "cap_xxx", "params": {}, "auto_fill_missing_params": false}'
```

The response is a `ReplayResult`:

```json
{
  "status": "success",              // "success" | "business_outcome" | "blocked" | "failed"
  "outcome_name": null,             // set when status == "business_outcome"
  "artifact_id": "cap_xxx",
  "run_id": "replay_abc123",
  "steps": [ { "step_id": "step_1", "ok": true, "attempts": 1, "screenshot": "/runtime_evidence/...", "duration_ms": 340 } ],
  "outputs": {},
  "used_params": {"username": "testuser_ab12cd", "password": "TestPass123!"},
  "auto_filled_params": ["username", "password"],
  "checkpoint_verified": true,      // best-effort heuristic — see replay.py's docstring
  "error": null,
  "duration_ms": 4210,
  "started_at": "...", "finished_at": "..."
}
```

- **Auto-filled params are fabricated test values, generated from the param NAME alone** (`username` → `testuser_ab12cd`, `ssn` → a fake SSN-shaped string, `zip_code` → a fake zip...) — never derived from any real prior run, consistent with this project's redaction stance. Every value actually used is visible in the response, nothing is a silent substitution. Uniqueness-sensitive fields (`username`, `email`) get a random suffix each call so repeated replays don't collide with an "already exists" business outcome.
- **Explicit values you supply always win** — auto-fill only covers genuine gaps.
- **Missing required params only fail fast** (before a browser is even launched) when `auto_fill_missing_params: false` is explicitly set.
- **Risky steps are blocked by default.** If the artifact's next step has `risky: true`, replay stops right before it with `status: "blocked"` unless you pass `"confirm_risky": true` in the request body — a capability being approved once during discovery doesn't make every future replay (with different params) automatically safe.
- **A step that keeps failing after retries + locator fallbacks** stops the whole run immediately with `status: "failed"` and names the step, the error, and how many attempts were made — replay never blindly continues into a page state the recorded steps didn't anticipate.
- **`known_outcomes`** is an optional field you can add to a saved artifact's JSON — `{"member_not_found": "No member found with that ID"}` — checked against the page after every step; a match stops replay with `status: "business_outcome"` instead of `"success"` or `"failed"`, since e.g. "no such member" is a legitimate answer, not a crash.

## Running without live services

There is no offline/mocked mode in this skeleton — `POST /api/discover` always
drives a real live page via Playwright and calls the real Groq API. If you don't
have a `GROQ_API_KEY`, the backend will start and `POST /api/discover` will still
return a `run_id` right away, but the very first status poll will show
`status: "done"`, `ok: false`, and a clear `reason` explaining the missing key
(see `GroqPlanner.__init__`) — the failure happens in the background task, not
the initial request.

## Config

| Env var | Default | Meaning |
| --- | --- | --- |
| `bank_api_interface` / `GROQ_API_KEY` | — | required; read by `planner.py` only, from a `.env` file or real env var (see above) |
| `BANKAI_MAX_STEPS` | `15` | discovery loop step cap |
| `BANKAI_HEADLESS` | `true` | set to `false` to watch the browser drive the page |
