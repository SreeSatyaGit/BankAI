# BankAI — Project Report

## Overview

BankAI is a computer-use agent for driving legacy, API-less web applications.
A user supplies a live target URL and a goal in plain English; an LLM drives
the page step by step to accomplish it, and the successful run is recorded as
a typed, versioned **Capability** artifact. That artifact can then be replayed
deterministically — no LLM in the loop — against the same site with new
parameters. There is no seeded target or mock data; the agent operates on
whatever URL is provided.

## Architecture

The system has two execution paths that share infrastructure but no control
flow:

- **Discovery** (`loop.py`) — LLM-driven, drives a live page via Playwright.
- **Replay** (`replay.py`) — executes a saved artifact with no LLM.

Both sit on a single seam, `Surface` (`surface.py`), exposing `perceive()` and
`act()` over a Playwright page; nothing above it touches Playwright directly.
`templating.py` (`{{param}}` substitution) and `evidence.py` (screenshot
capture) are shared by both paths.

The FastAPI layer (`main.py`) runs discovery as a background task —
`POST /api/discover` returns a `run_id` immediately and the client polls
`GET /api/discover/{run_id}`, because a run can pause for human input for an
unbounded time. `POST /api/replay` is a single blocking call, since replay
has no LLM and no pause and completes in bounded time. `runs.py` holds the
in-memory run registry and the pause/resume primitive.

## Artifact schema

The persisted unit is `Capability` (`schema.py`): id, version, goal,
target_url, `params`/`outputs` (names only), an ordered list of `StepSpec`, a
`success_checkpoint`, `created_at`, and an optional human-curated
`known_outcomes` map.

Each `StepSpec` carries an id, description, one `Action`, a free-text
checkpoint, and a `risky` flag. Locators are semantic — `label`, `role`+name,
`placeholder`, visible `text`, or the HTML `name` attribute — never CSS or
xpath, with an ordered `fallbacks` chain so a step can still resolve if its
primary strategy stops matching.

Sensitive input never reaches disk. During a run, a step's transient
`param_bindings` holds the literal values typed into the page; `loop.py`
strips them before the step is persisted, so only parameter *names* survive
into the artifact. This was verified against the saved ParaBank registration
artifact: 11 parameters (including `ssn` and `password`) are declared by name,
and no typed value appears anywhere in the file.

## Determinism & error handling

Replay handles two distinct failure modes with two mechanisms: `Locator`
fallbacks for structural drift (an element that moved but is still findable),
and a bounded per-step retry with linear backoff for transient timing. If both
are exhausted, the step is a hard failure and the run stops immediately rather
than continuing into an unexpected page state.

Every replay returns a structured `ReplayResult` with one of four statuses:

- **success** — all steps ran, no known business outcome matched.
- **business_outcome** — a curated `known_outcomes` pattern matched (checked
  after every step), i.e. a legitimate result such as "no such member," not a
  crash.
- **blocked** — a `risky` step was reached without `confirm_risky`, so replay
  stopped before executing it.
- **failed** — a step exhausted its retries, with the failing step, attempt
  count, and error reported.

## Heterogeneity & multi-tenant

`Surface` is the intended extension point for other backends (legacy web,
desktop) — the loop and replay engine only ever call `perceive()`/`act()`.
Semantic locators are the enabling choice: `perceive()` reports which strategy
resolves each field rather than guessing. The ParaBank artifact bears this out
— every form field resolves by its `name` attribute because the page exposes
no real labels, a case CSS-based targeting would not handle cleanly.

Multi-tenant support (one capability generalized across differently-branded
instances of the same app) is out of scope for this version.

## Escalation & handoff

Each run holds one live Playwright page. When discovery pauses, a human acts
on that exact session, not a fresh one, through two endpoints:
`POST .../manual-action` (perform any `Action` — click, type, navigate,
extract — repeatable) and `POST .../resume` (answer the pending escalation).
Three situations pause a run: a `risky` step, a step that exhausted its
retries, and the planner getting stuck; each offers the decision set that
makes sense for it. Because the pause is an `asyncio.Event` on a cooperative
single-threaded loop, the run is genuinely suspended while a human acts — no
locking required.

Replay has no live-pause path by design; a failure is returned in the response
for the caller to handle.

## Safety

Two guarantees. First, no sensitive data is persisted — parameter values are
templated as `{{name}}` and stripped before the artifact is written, confirmed
against a real run containing an SSN and password. Second, risky/irreversible
steps are gated: discovery pauses for human approval before executing one, and
replay refuses to run one unless the caller passes `confirm_risky=true` on
that specific call — approval at discovery time does not carry over to future
replays with different parameters. Auto-filled replay parameters
(`defaults.py`) are fabricated from the parameter name alone, never from prior
real values, and every value used is reported back so nothing is silently
substituted.

## Testing & evidence

The test suite (`tests/`) covers store round-tripping, discovery
persist-on-success and no-persist-on-failure, param-binding stripping, planner
parse-failure recovery, and failure-path handling, using fakes for Playwright,
Groq, and the network. `runtime_evidence/` holds captured screenshots from a
discovery run and multiple replay runs, including a `blocked` replay stopping
at the risky Register step.

## Status

Discovery, deterministic replay, human-in-the-loop escalation, the typed
artifact format, and the safety guarantees are all implemented and exercised
end to end against a live target (ParaBank). The browser is the one
implemented `Surface`; legacy-web and desktop backends, multi-tenant reuse,
and automatic population of `known_outcomes` are identified extension points
rather than built features.