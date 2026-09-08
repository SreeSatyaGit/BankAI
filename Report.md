# Design report

## Architecture

Two execution paths, no shared control flow:

- **Discovery** (`loop.py`) — LLM in the loop, drives a live page via Playwright. No seeded target; any URL + goal.
- **Replay** (`replay.py`) — runs a saved artifact, no LLM. Doesn't import `planner.py`.

Both sit on `Surface` (`surface.py`): `perceive()`/`act()` over a Playwright `Page`. Neither path touches Playwright directly. `templating.py` and `evidence.py` are shared between both paths.

API layer: `POST /api/discover` runs as a background task, returns `run_id` immediately (a run can pause indefinitely on escalation — can't hold an HTTP request open for that). Frontend polls `GET /api/discover/{run_id}`. `POST /api/replay` is a blocking call — no LLM, no pause, bounded time.

Escalation is two endpoints, not one:
- `POST /api/discover/{run_id}/manual-action` — human acts on the live page directly, same `Action` schema the planner uses, any number of times.
- `POST /api/discover/{run_id}/resume` — answers the pending escalation (`approve`/`skip`/`continue`/`retry`/`abort`, depending on trigger).

`runs.py` holds the run registry and the pause/resume primitive (`asyncio.Event` per run).

## Artifact schema

`Capability`: id, version, goal, target_url, `params`/`outputs` (names only), ordered `steps`, `success_checkpoint` (free text), `created_at`, `known_outcomes` (optional, human-curated, empty by default).

`StepSpec`: id, description, one `Action`, `param_bindings` (transient), `checkpoint` (free text), `risky` flag. `Action`: `{type, locator, value, output_name}`. `Locator`: `label`/`role`/`placeholder`/`text`/`name`, no CSS/xpath, with a `fallbacks` chain.

Checked against the one real saved artifact (`create-an-account__cap_57f3f830aa07.json`, 15 steps, ParaBank registration): 11 params declared (first_name, last_name, address, city, state, zip_code, phone_number, ssn, username, password, password_confirm), every persisted `param_bindings` is `{}`, final "Register" step is the only one flagged `risky: true`. SSN and password went into the real form; neither is in the saved file.

Mechanism: `param_bindings` holds `{name: literal}` for the current run only. `loop.py`'s `_strip_for_persistence` clears it before write. Not a redaction pass — the value never gets serialized.

## Determinism & error handling

Replay: two robustness mechanisms, two failure modes.
- Structural (element moved, still findable another way) → `Locator.fallbacks`.
- Temporal (element correct, page not settled) → retry, `MAX_ATTEMPTS = 3`, linear backoff.

Both exhausted → hard failure, stop immediately.

Outcome priority per step: `known_outcomes` checked first (title+url+digest substring match), after *every* step, not just the end — catches a mid-flow business result before later steps run against a page that already answered. No match → `success`, plus `checkpoint_verified` (keyword-overlap on `success_checkpoint`, threshold 0.4) — explicitly documented as a signal, not proof.

Discovery escalates on three triggers: `risky` step, `retry_exhausted`, `PlannerStuck`. Does NOT escalate on `PlannerError` (Groq API call failed twice) — a human can't fix a broken API call, run just ends. Planner distinguishes transport failure from unparseable output (`_PlannerOutputUnparseable`, Groq's `output_parse_failed`) — the latter gets one corrective retry before `PlannerStuck`, since it's sometimes human-fixable (e.g. a Cloudflare wall).

**Bug**: `_retry_after_api_failure` catches `except _PlannerCallFailed`, and `_PlannerOutputUnparseable` is a subclass of it. First call fails on transport → routes here. If the retry then returns `output_parse_failed` (not transport, just unparseable), it gets caught by the broad except and mislabeled as terminal `PlannerError`, instead of getting the same corrective-retry treatment the first-call path gives it. Narrow scenario, but contradicts this file's own stated design. Not fixed — flagging only.

## Heterogeneity & multi-tenant

`Surface` is the extension point. Locators are semantic because CSS/xpath doesn't survive on legacy/desktop targets. `perceive()` reports which strategy resolves each field instead of making the planner guess — confirmed by the real artifact: all 11 form-field steps use `strategy: "name"`, because ParaBank's form has no real `<label>` elements. CSS-based locators would not have worked here.

No multi-tenant support. One `target_url` per run, no tenant config, no override mechanism. Unstarted, not partial.

## Escalation & handoff

One live Playwright page per run (`RunState.live_surface`). Paused run → manual-action and resume act on that exact page, not a new session. Concurrency is correct: `request_intervention()` awaits an `asyncio.Event`; `asyncio` is cooperative/single-threaded, so `run_discovery()` is genuinely suspended while a manual action runs. No locking needed.

Three triggers, three different decision sets — `risky` (approve/skip/abort), `retry_exhausted` (retry/skip/abort), `stuck` (continue/abort). Frontend renders the correct buttons per trigger. A human can call manual-action repeatedly before resuming at all.

Replay has none of this by design — no long-lived wait to justify it. A failure just comes back in the response (`blocked` for unconfirmed risky, `failed` otherwise) for the caller to handle.

## Safety

Core guarantee holds against a real run with actual sensitive fields (SSN, password) — verified directly against the saved artifact, not just the docstring's claim.

Risky-step handling differs by path, correctly: discovery pauses and asks before executing. Replay refuses unless `confirm_risky=true` per call — not inherited from discovery-time approval, since different params (a different transfer amount) is a different decision each time.
