"""
The discovery loop. This is where an LLM-driven run against a REAL live page
happens: navigate to the pasted URL, then repeatedly perceive -> ask the planner
for a step -> execute it, until the planner signals done, we hit the max-steps
cap, or a human aborts an escalation. On success, the executed steps are
assembled into a Capability artifact and persisted via store.save().

Deterministic replay (executing a saved Capability WITHOUT the LLM) lives in
replay.py — a separate engine, since it's a fundamentally different execution
mode (no planner in the loop at all). This file only produces the artifact; it
does not consume one.

Human-in-the-loop escalation: three situations pause the run and await a
human decision via `request_intervention` (main.py wires this to
RunState.request_intervention, a real asyncio-based pause — see runs.py):
  - "risky"          a step the planner marked risky=True
  - "retry_exhausted" a step that still failed after MAX_STEP_ATTEMPTS retries
  - "stuck"           the planner itself gave up (PlannerStuck) — a genuine
                      semantic dead end (explicit finish(status="stuck"), or
                      the model returning unparseable output twice running).
                      A human can act directly on the SAME live session (see
                      on_surface_ready below) to clear whatever blocked the
                      agent — dismiss a popup, log in, pick a disambiguating
                      option — then hand back control ("continue") for the
                      agent to keep going, or give up ("abort").
A step failing outright used to just get logged for the planner to notice
and adapt to next turn; it now gets a bounded mechanical retry first
(handles transient issues fast, without spending an LLM call), and only
escalates to a human if that's not enough — plausibly a sign the planner's
whole approach for that step is broken, not just mistimed.

PlannerError (the Groq API call itself failed twice in a row) is NOT
escalated — it's an automatic, terminal failure. A human at the keyboard
can't repair a broken API call by acting on the page, so there's nothing a
handoff would accomplish; the run just ends with ok=False and the reason.

Deliberately NOT built here: replay has no equivalent live-pause capability.
A step failing during replay is a bounded hard failure returned in the
response for the CALLER to act on afterward, not a live human takeover mid-
call — see replay.py's docstring and REPORT.md's Cuts section for why.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from playwright.async_api import async_playwright

from . import store
from .evidence import capture_screenshot
from .planner import GroqPlanner, PlannerDone, PlannerError, PlannerStuck
from .schema import Action, Capability, Perception, StepSpec
from .surface import Surface
from .templating import substitute

MAX_STEPS = int(os.environ.get("BANKAI_MAX_STEPS", "25"))
HEADLESS = os.environ.get("BANKAI_HEADLESS", "true").lower() != "false"

MAX_STEP_ATTEMPTS = 3  # 1 initial try + up to 2 mechanical retries before escalating
RETRY_BACKOFF_MS = 400

RequestInterventionFn = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
OnSurfaceReadyFn = Callable[[Surface, Path], None]


async def _default_intervention_handler(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Safe default when no human is actually wired up: auto-approve a risky
    step (matches the old auto-approve behavior), but ABORT on stuck/retry-
    exhausted rather than guessing — those have no sensible unattended default."""
    if payload.get("trigger") == "risky":
        return {"decision": "approve"}
    return {"decision": "abort", "note": "no intervention handler wired up"}


def _dedupe_step_id(step_id: str, used_ids: Set[str]) -> str:
    """Returns a step_id guaranteed unique against used_ids — suffixing _2,
    _3, ... if the planner reused an id (seen on long runs where it loses
    count). Mutates used_ids to include whatever id is returned. This runs at
    the moment each StepSpec is received, BEFORE that id is used for
    anything — screenshot filenames, event logs, or eventual persistence — so
    a collision can never silently overwrite earlier evidence."""
    if step_id not in used_ids:
        used_ids.add(step_id)
        return step_id
    n = 2
    candidate = f"{step_id}_{n}"
    while candidate in used_ids:
        n += 1
        candidate = f"{step_id}_{n}"
    used_ids.add(candidate)
    return candidate


def _strip_for_persistence(step: StepSpec) -> StepSpec:
    """Return a copy of `step` with param_bindings cleared — those hold the raw
    literal values typed during discovery and must never be written to disk."""
    return step.model_copy(update={"param_bindings": {}})


def _build_intervention_payload(
    trigger: str,
    reason: str,
    perception: Perception,
    history: List[Dict[str, Any]],
    step: Optional[StepSpec] = None,
    resolved_action: Optional[Action] = None,
    screenshot: Optional[str] = None,
) -> Dict[str, Any]:
    """Everything a human needs to make an informed decision without having
    to reconstruct context from scratch: what triggered this, what the
    agent's current view of the page is, what it's tried recently, and (when
    there is one) the specific step in question."""
    return {
        "trigger": trigger,  # "risky" | "retry_exhausted" | "stuck"
        "reason": reason,
        "step": step.model_dump() if step else None,
        "resolved_action": resolved_action.model_dump() if resolved_action else None,
        "perception": perception.model_dump(),
        "history_tail": history[-5:],
        "screenshot": screenshot,
    }


async def _execute_with_retry(
    surface: Surface, action: Action, max_attempts: int
) -> tuple:
    """Bounded mechanical retry for transient issues (page not settled yet,
    a slow animation) — cheap and fast, tried before spending a human's
    attention. Returns (ActionResult, attempts_made)."""
    result = None
    for attempt in range(1, max_attempts + 1):
        result = await surface.act(action)
        if result.ok:
            return result, attempt
        if attempt < max_attempts:
            await asyncio.sleep(RETRY_BACKOFF_MS / 1000 * attempt)
    return result, max_attempts


async def run_discovery(
    target_url: str,
    goal: str,
    run_id: Optional[str] = None,
    events: Optional[List[Dict[str, Any]]] = None,
    request_intervention: Optional[RequestInterventionFn] = None,
    on_surface_ready: Optional[OnSurfaceReadyFn] = None,
    planner: Optional[GroqPlanner] = None,
) -> Dict[str, Any]:
    # `planner` is injectable so tests can drive the loop with a scripted
    # sequence of outcomes and no Groq key / network; production always lets it
    # default to a real GroqPlanner.
    planner = planner or GroqPlanner()

    run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
    # `events` is caller-owned when provided (e.g. main.py hands in a RunState's
    # events list so a GET /api/discover/{run_id} poll sees progress live, not
    # just once the whole run finishes) — we only append, never replace it.
    events = events if events is not None else []
    request_intervention = request_intervention or _default_intervention_handler
    run_dir = store.RUNTIME_EVIDENCE_DIR / run_id

    history: List[Dict[str, Any]] = []
    executed_steps: List[StepSpec] = []
    params: Dict[str, str] = {}
    outputs: Dict[str, str] = {}
    used_step_ids: Set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS)
        page = await browser.new_page()
        surface = Surface(page)
        if on_surface_ready:
            on_surface_ready(surface, run_dir)

        try:
            await page.goto(target_url, timeout=30000)
        except Exception as exc:  # noqa: BLE001
            await browser.close()
            return {
                "ok": False,
                "steps": events,
                "artifact": None,
                "reason": f"could not load target_url: {exc}",
                "run_id": run_id,
            }

        final_reason = "max steps reached"
        final_checkpoint: Optional[str] = None
        success = False

        try:
            for _ in range(MAX_STEPS):
                perception = await surface.perceive()
                # planner.next_step() is a blocking, synchronous Groq call
                # (with its own retries/backoff). Run it off the event loop so
                # the FastAPI server keeps answering status polls while the
                # planner is slow or failing — otherwise a stalled/failing API
                # call freezes uvicorn and the frontend's poll returns 500
                # instead of the run's real ok=False + reason.
                outcome = await asyncio.to_thread(
                    planner.next_step,
                    goal=goal,
                    param_names=sorted(params.keys()),
                    perception=perception,
                    history=history,
                )

                if isinstance(outcome, PlannerStuck):
                    stuck_screenshot = await capture_screenshot(page, run_dir, "stuck")
                    payload = _build_intervention_payload(
                        trigger="stuck",
                        reason=outcome.reason,
                        perception=perception,
                        history=history,
                        screenshot=stuck_screenshot,
                    )
                    events.append({"type": "intervention_requested", **payload})
                    decision = await request_intervention(payload)  # <-- suspends here; human can act on live_surface
                    events.append({"type": "intervention_resolved", "trigger": "stuck", **decision})

                    if decision.get("decision") == "abort":
                        final_reason = decision.get("note") or outcome.reason
                        break

                    history.append(
                        {
                            "step": "(human intervention)",
                            "result": "continue",
                            "detail": decision.get("note") or "human intervened after the agent got stuck",
                        }
                    )
                    continue

                if isinstance(outcome, PlannerError):
                    error_screenshot = await capture_screenshot(page, run_dir, "planner_error")
                    events.append({"type": "planner_error", "reason": outcome.reason, "screenshot": error_screenshot})
                    final_reason = outcome.reason
                    break

                if isinstance(outcome, PlannerDone):
                    final_reason = outcome.reason
                    final_checkpoint = outcome.final_checkpoint
                    success = True
                    screenshot = await capture_screenshot(page, run_dir, "done")
                    events.append({"type": "done", "reason": outcome.reason, "screenshot": screenshot})
                    break

                step: StepSpec = outcome

                deduped_id = _dedupe_step_id(step.id, used_step_ids)
                if deduped_id != step.id:
                    events.append(
                        {
                            "type": "step_id_deduped",
                            "original_step_id": step.id,
                            "deduped_step_id": deduped_id,
                        }
                    )
                    step = step.model_copy(update={"id": deduped_id})

                params.update(step.param_bindings)
                try:
                    resolved_action = step.action.model_copy(
                        update={"value": substitute(step.action.value, params)}
                    )
                except KeyError as exc:
                    events.append({"type": "step_error", "step_id": step.id, "error": str(exc)})
                    history.append(
                        {"step": step.description, "result": "error", "detail": str(exc)}
                    )
                    continue  # let the planner see this and course-correct or give up

                if step.risky:
                    pending_screenshot = await capture_screenshot(page, run_dir, f"{step.id}_pending")
                    payload = _build_intervention_payload(
                        trigger="risky",
                        reason=f"step {step.id} is marked risky",
                        perception=perception,
                        history=history,
                        step=step,
                        resolved_action=resolved_action,
                        screenshot=pending_screenshot,
                    )
                    events.append({"type": "intervention_requested", **payload})
                    decision = await request_intervention(payload)
                    events.append({"type": "intervention_resolved", "trigger": "risky", "step_id": step.id, **decision})

                    verdict = decision.get("decision", "abort")
                    if verdict == "abort":
                        final_reason = decision.get("note") or f"human aborted at risky step {step.id}"
                        break
                    if verdict == "skip":
                        history.append(
                            {
                                "step": step.description,
                                "action_type": step.action.type,
                                "result": "denied",
                                "detail": decision.get("note") or "a human reviewer denied this risky step",
                            }
                        )
                        continue

                result, attempts = await _execute_with_retry(surface, resolved_action, MAX_STEP_ATTEMPTS)

                if not result.ok:
                    exhausted_screenshot = await capture_screenshot(page, run_dir, f"{step.id}_retry_exhausted")
                    payload = _build_intervention_payload(
                        trigger="retry_exhausted",
                        reason=f"step {step.id} failed after {attempts} attempt(s): {result.error}",
                        perception=perception,
                        history=history,
                        step=step,
                        resolved_action=resolved_action,
                        screenshot=exhausted_screenshot,
                    )
                    events.append({"type": "intervention_requested", **payload})
                    decision = await request_intervention(payload)
                    events.append({"type": "intervention_resolved", "trigger": "retry_exhausted", "step_id": step.id, **decision})

                    verdict = decision.get("decision", "abort")
                    if verdict == "abort":
                        final_reason = decision.get("note") or payload["reason"]
                        break
                    if verdict == "retry":
                        result, extra_attempts = await _execute_with_retry(surface, resolved_action, 1)
                        attempts += extra_attempts
                        if not result.ok:
                            history.append(
                                {
                                    "step": step.description,
                                    "action_type": step.action.type,
                                    "result": "error",
                                    "detail": f"still failing after human-assisted retry: {result.error}",
                                }
                            )
                            continue
                    else: 
                        history.append(
                            {
                                "step": step.description,
                                "action_type": step.action.type,
                                "result": "error",
                                "detail": f"{result.error} (human chose to move on)",
                            }
                        )
                        continue

                if result.ok and resolved_action.type == "extract" and resolved_action.output_name:
                    outputs[resolved_action.output_name] = result.extracted_text or ""

                screenshot = await capture_screenshot(page, run_dir, step.id)

                events.append(
                    {
                        "type": "step",
                        "step_id": step.id,
                        "description": step.description,
                        "action_type": step.action.type,
                        "ok": result.ok,
                        "error": result.error,
                        "risky": step.risky,
                        "attempts": attempts,
                        "screenshot": screenshot,
                    }
                )
                history.append(
                    {
                        "step": step.description,
                        "action_type": step.action.type,
                        "result": "ok" if result.ok else "error",
                        "detail": result.error,
                    }
                )

                executed_steps.append(step)

        finally:
            await browser.close()

    artifact: Optional[Capability] = None
    if success and executed_steps:
        artifact = Capability(
            id=f"cap_{uuid.uuid4().hex[:12]}",
            version=1,
            goal=goal,
            target_url=target_url,
            params=sorted(params.keys()),
            outputs=sorted(outputs.keys()),
            steps=[_strip_for_persistence(s) for s in executed_steps],
            success_checkpoint=final_checkpoint,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        store.save(artifact)

    return {
        "ok": success,
        "steps": events,
        "artifact": artifact.model_dump() if artifact else None,
        "reason": None if success else final_reason,
        "run_id": run_id,
    }
