# BankAI

A minimal, real skeleton for a computer-use agent: paste a live target URL and a
goal in plain English, and a Python backend uses an LLM (Groq) to drive that page
step by step until the goal is met (or it honestly gives up), recording the
successful run as a typed, replayable **Capability** artifact.

**Status:** discovery path only. Replay (running a saved artifact without the LLM)
is stubbed — see `POST /api/replay` in `backend/main.py`.

There is no built-in target, no demo bank, no seeded data. You paste a real URL;
the agent drives whatever is actually there.

## How it works

```
target_url + goal
      │
      ▼
 loop.py  ── perceive() ──▶  surface.py (Playwright, semantic locators)
   │  ▲                            │
   │  └──── act(step) ─────────────┘
   ▼
 planner.py (GroqPlanner — the ONLY file that talks to an LLM)
   │
   ▼
 on success: Capability artifact ──▶ store.py ──▶ artifacts/*.json
```

- `backend/schema.py` — the typed contracts (Locator, Action, StepSpec, Perception, Capability). No logic.
- `backend/surface.py` — perceive()/act() over a live Playwright page, using semantic locators (label / role+name / placeholder / text), not CSS/xpath.
- `backend/planner.py` — `GroqPlanner`, the only place the LLM lives. Tool-calling with `next_step` / `finish`.
- `backend/loop.py` — the discovery loop: perceive → planner.next_step → act, until done/stuck/max-steps. Assembles and persists the artifact.
- `backend/store.py` — flat-file JSON persistence for artifacts.
- `backend/main.py` — FastAPI endpoints.

Raw values you don't want frozen into an artifact (a member id, an amount, anything
that looks like PII) are never written to disk: the planner references them as
`{{param_name}}` in `action.value` and supplies the literal separately via
`param_bindings`, which `loop.py` strips before persisting the `StepSpec`. Only
param *names* survive into `Capability.params`.

## Setup

### Backend

```bash
cd bankai
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env               # then edit .env and set GROQ_API_KEY
export GROQ_API_KEY=your_key_here  # or rely on .env if you load it yourself

uvicorn backend.main:app --reload --port 8000
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

curl -s http://127.0.0.1:8000/api/artifacts | python3 -m json.tool
```

Replay is not implemented yet:

```bash
curl -s -X POST http://127.0.0.1:8000/api/replay \
  -H 'Content-Type: application/json' \
  -d '{"artifact_id": "cap_xxx", "params": {}}'
# -> 501, with a comment in backend/main.py explaining it's the next milestone
```

## Running without live services

There is no offline/mocked mode in this skeleton — `POST /api/discover` always
drives a real live page via Playwright and calls the real Groq API. If you don't
have a `GROQ_API_KEY`, the backend will start, but `/api/discover` will fail fast
with a clear error explaining the missing key (see `GroqPlanner.__init__`).

## Config

| Env var | Default | Meaning |
| --- | --- | --- |
| `GROQ_API_KEY` | — | required; read by `planner.py` only |
| `BANKAI_MAX_STEPS` | `15` | discovery loop step cap |
| `BANKAI_HEADLESS` | `true` | set to `false` to watch the browser drive the page |
