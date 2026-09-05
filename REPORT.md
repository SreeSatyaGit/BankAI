# Design report

## Architecture

The system is one loop split by a hard boundary. On the discovery side, an LLM
planner drives a live UI to accomplish a natural-language goal and every action
it takes is captured. On the execution side, a deterministic engine replays the
captured plan with no model in the loop. The artifact is the contract between
the two, and it is the only thing that crosses the boundary.

Four seams keep the pieces independent. The **Surface** (`surface/base.py`)
abstracts "perceive the current state / act on a locator", so nothing above it
knows whether it is talking to server-rendered HTML, a browser, or an
accessibility tree. The **Planner** (`agent/planner.py`) is the only place a
model ever runs; it emits fully-typed `StepSpec`s, so the recorder never parses
a raw transcript and replay never needs a model. The **artifact**
(`artifact/schema.py`) is a typed, versioned capability. The **Replayer**
(`replay/engine.py`) is a generic interpreter of that artifact. Cross-cutting
concerns — a **Policy**/redaction layer, a redacting structured logger, and an
escalation coordinator — are injected into both loops.

The bundled target, `CoreServ 7.2`, is a deliberately legacy Flask app: server-
rendered, table layouts, no test IDs, controls identified only by visible label
text, plus injectable failure states so every error path is demonstrable.

The primary surface drives the app over HTTP and parses the returned HTML. For
the large class of legacy server-rendered back-office apps this is more
deterministic and far cheaper than a browser, and it needs nothing installed.
The browser/accessibility surface is designed against the same interface (see
Heterogeneity) so the artifact and replay engine do not change.

An optional web frontend ("Bridge", in `frontend/`) sits on top of a thin JSON
API (`src/cua/api.py`) that wraps the same discover/replay pipeline the CLI
calls — included for interactive exploration, not part of the graded core.

## Artifact schema

An artifact is a *capability contract*, not a recorded macro. It is meant to be
read both by a calling agent that needs a typed signature and by a human
reviewer who needs to see what it does and how it can fail. The full model is in
`artifact/schema.py`; a discovered example is `artifacts/read_savings_balance@1.0.0.json`.

The shape: identity and semantic `version`; a `target` naming the logical app
(the vendor product, not a tenant URL) and entry point; typed `params` and
`outputs`; an ordered list of `steps`; a `success` checkpoint; a declarative
`outcomes` taxonomy; and `provenance` linking back to the discovery run.

Four choices carry most of the weight:

* **Semantic locators with a fallback chain.** A locator is a strategy plus a
  value — a *label*, *button*/*link* text, or a table *row_value* — never a CSS
  path or pixel coordinate, and it carries an ordered list of fallbacks. Semantic
  locators are what survive tenant re-branding and what a reviewer can actually
  read; the fallback chain absorbs cosmetic drift, and replay records which link
  in the chain resolved.
* **Error taxonomy as data.** Every artifact declares its own `outcomes`, each a
  `(detector, kind, recovery?)` triple. Replay is a generic interpreter of this
  list, so a new capability needs no new replay code.
* **Params are templated, never frozen.** Inputs appear in steps as `{{name}}`.
  The recorder infers the `params` from those templates, so the concrete values
  that drove discovery — which may be PII — are never written into the artifact.
* **Checkpoints are first-class.** Steps carry optional post-conditions and the
  capability carries a success checkpoint, so replay verifies it actually
  reached the intended state rather than assuming a click worked.

Versioning is by `id@version` on disk with an `approval` field; unattended
replay is gated on `approved`. Params/outputs carry a `sensitive` flag that
drives redaction.

## Determinism & error handling

Replay consults no model and makes no probabilistic choice. Given the same
server state it resolves the same locators (primary then fallback), runs the
same actions, and verifies the same checkpoints. The engine is a pure
interpreter of the artifact.

The result contract is deliberately **tri-state**, because the most common
mistake in this space is conflating a business outcome with a crash:

* `SUCCESS` — reached the success checkpoint; returns typed outputs.
* `BUSINESS_OUTCOME` — a legitimate result the caller must handle (e.g. "no such
  member", "not authorized", "field required"); **not** a failure.
* `FAILURE` — a hard failure, carrying the step, what was expected, what was
  observed, and a path to a saved snapshot.

Runtime conditions are classified by the artifact's taxonomy into three kinds.
**Business** outcomes stop cleanly and return the second result class.
**Recoverable** conditions are handled and execution continues — a *dismiss*
(clear an interstitial), a bounded *retry* (re-load on a transient 5xx), or a
bounded *wait* — after which the engine re-checks that the condition actually
cleared. **Hard** outcomes stop; if a human is configured they escalate,
otherwise they fail. A state that matches *no* detector but breaks a checkpoint
is treated as unexpected: escalate if possible, and only then fail. The demo
exercises all of these: a transient 503 recovers via retry, a privacy
interstitial via dismiss, unknown/authorization states as business outcomes, and
a maintenance state as an escalation-or-failure.

## Heterogeneity & multi-tenant

Heterogeneity of *surface* is handled by the `Surface` seam. Because every
strategy is semantic, it maps cleanly onto other backends: `label` →
`get_by_label`, `button`/`link` → `get_by_role`, `row_value` → a row's cell —
all of which are ARIA roles and accessible names, which is also exactly what an
OS accessibility tree exposes for native desktop apps, and what a
screenshot+coordinates agent would resolve to a bounding box.
`surface/playwright_surface.py` documents this mapping. Swapping the surface
changes no artifact and no replay code; only `target.surface_kind` differs.

Heterogeneity of *tenant* — many institutions running the same vendor product
with cosmetic differences — is handled at two levels. The fallback chains absorb
most drift for free. For the rest, the schema includes a `TenantBinding` hook:
a base capability is recorded once against the vendor `app_id`, and per-tenant
bindings supply the concrete `base_url` and any locator/route overrides keyed by
step id. This keeps one reviewed capability per vendor flow instead of one per
institution. The binding type is defined but its resolution is a documented cut
(below). A natural next step is route canonicalization (`/member/12345` →
`/member/:id`) so navigations are tenant-independent.

## Escalation & handoff

Escalation is modelled as a single explicit "who is in control" token over
**one live session**. When replay hits a state it cannot safely resolve, it
builds an `InterventionRequest` carrying the context a human needs (capability,
goal, current step, current perception, snapshot, reason), cedes control, and
hands the **same `Surface` handle** to an operator. The operator acts on that
live session, control returns with a decision (`resume`/`abort`) and a record of
exactly what the human did, and on `resume` the engine re-attempts the step and
re-verifies the checkpoint before continuing.

The key property is that this is not a fresh session: because every layer
already operates on an injected `Surface` handle, handing that same handle to
the operator *is* the control transfer — no new login, no lost context. The demo
shows member `77777` landing on a maintenance state the automation was never
taught about; the step fails its checkpoint, replay escalates, the operator
clears a supervisor override on the live session, and replay resumes to success.
`MockOperator` stands in for what would be a co-browsing console (a shared
browser over CDP/VNC with a human UI); the console is out of scope but the
control model and the same-session guarantee are real.

## Safety

Three guardrails apply in both loops. An **allowlist policy** constrains
navigable hosts/paths and permitted action types and is checked before every
action. **Risky-action gating**: state-changing/irreversible steps (e.g. the
"Create sub-account" submit) are flagged on the artifact and, in unattended
replay, blocked unless explicitly confirmed or human-approved — the demo shows
the same capability failing closed without `--confirm-risky` and succeeding with
it. **Redaction**: everything written to logs or snapshots passes through a
redactor that masks explicitly-sensitive params/outputs entirely and scrubs
common PII/secret patterns (SSNs, card numbers, credentials, emails). Sensitive
values are returned to the caller in the clear but never persisted — verified in
the demo: the password and account balances produce zero cleartext hits on disk
while the caller still receives the real balance.

The honest limit: this is a policy *decision point*, not a sandbox. It governs
what the agent is permitted to attempt; it does not by itself contain a
compromised surface or a hostile page, and redaction is pattern-plus-flag based
rather than a guarantee. Network egress limits, per-capability scopes, and
signed/reviewed artifacts would harden a real deployment.

## Cuts

Scoped out deliberately to keep a thin but complete vertical slice:

* **Browser / accessibility / desktop surfaces** are designed against the
  `Surface` seam but only the HTTP/HTML surface is implemented; the Playwright
  stub documents the mapping.
* **The LLM planner is not exercised in the offline demo.** `MockPlanner` is a
  deterministic stand-in with the identical `StepSpec` output contract;
  `ClaudePlanner` is wired for `ANTHROPIC_API_KEY` but untested here.
* **Multi-tenant** is a schema hook (`TenantBinding`) plus fallback chains;
  binding resolution and route canonicalization are not implemented.
* **The operator console is mocked.** The control-transfer mechanism is real; a
  human-facing co-browsing UI is not built.
* **Store is flat files** with `id@version.json` and an approval flag — no real
  registry, no signing, no migration tooling.
* **Discovery-time outcome proposal**: the error taxonomy is attached from a
  curated default rather than proposed by the model during discovery and
  reviewed. In production these would be suggested during discovery and approved
  alongside the artifact.
