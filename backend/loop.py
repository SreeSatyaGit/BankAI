"""
The discovery loop. This is where an LLM-driven run against a REAL live page
happens: navigate to the pasted URL, then repeatedly perceive -> ask the planner
for a step -> execute it, until the planner signals done/stuck or we hit the
max-steps cap. On success, the executed steps are assembled into a Capability
artifact and persisted via store.save().

Replay (executing a saved Capability WITHOUT the LLM) is intentionally NOT
implemented here — see the stub and comment on POST /api/replay in main.py.
This file only produces the artifact; it does not consume one.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from playwright.async_api import async_playwright

from . import store
from .planner import GroqPlanner, PlannerDone, PlannerStuck
from .schema import Action, Capability, StepSpec
from .surface import Surface

MAX_STEPS = int(os.environ.get("BANKAI_MAX_STEPS", "15"))
HEADLESS = os.environ.get("BANKAI_HEADLESS", "true").lower() != "false"

# Called before executing any step with risky=True. Given {"step", "resolved_action",
# "screenshot"} for display, returns True to proceed or False to skip the step.
# main.py wires this to RunState.request_confirmation (a real pause on a human
# answer); the default below auto-approves, for direct/scripted callers that don't
# care about human-in-the-loop (e.g. tests).
ConfirmRiskyFn = Callable[[Dict[str, Any]], Awaitable[bool]]


async def _auto_approve(_: Dict[str, Any]) -> bool:
    return True


_PARAM_REF = re.compile(r"\{\{(\w+)\}\}")


def _substitute(value: Optional[str], params: Dict[str, str]) -> Optional[str]:
    """Replace every {{name}} in `value` with params[name]. Raises if a referenced
    param was never bound — a planner bug we want to surface loudly, not paper over."""
    if value is None:
        return None

    def _sub(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in params:
            raise KeyError(f"step referenced unbound param '{{{{{name}}}}}'")
        return params[name]

    return _PARAM_REF.sub(_sub, value)


def _strip_for_persistence(step: StepSpec) -> StepSpec:
    """Return a copy of `step` with param_bindings cleared — those hold the raw
    literal values typed during discovery and must never be written to disk."""
    return step.model_copy(update={"param_bindings": {}})


async def _capture_screenshot(page, run_dir: Path, name: str) -> Optional[str]:
    """Best-effort screenshot for evidence. Never fails the run — a screenshot
    that couldn't be taken (e.g. page mid-navigation) just means no visual for
    that moment, not a broken discovery run."""
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"{name}.png"
        await page.screenshot(path=str(path), timeout=5000)
        return f"/runtime_evidence/{run_dir.name}/{name}.png"
    except Exception:  # noqa: BLE001
        return None


async def run_discovery(
    target_url: str,
    goal: str,
    run_id: Optional[str] = None,
    events: Optional[List[Dict[str, Any]]] = None,
    confirm_risky: Optional[ConfirmRiskyFn] = None,
) -> Dict[str, Any]:
    planner = GroqPlanner()

    run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
    # `events` is caller-owned when provided (e.g. main.py hands in a RunState's
    # events list so a GET /api/discover/{run_id} poll sees progress live, not
    # just once the whole run finishes) — we only append, never replace it.
    events = events if events is not None else []
    confirm_risky = confirm_risky or _auto_approve
    run_dir = store.RUNTIME_EVIDENCE_DIR / run_id

    history: List[Dict[str, Any]] = []
    executed_steps: List[StepSpec] = []
    params: Dict[str, str] = {}
    outputs: Dict[str, str] = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS)
        page = await browser.new_page()
        surface = Surface(page)

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
                outcome = planner.next_step(
                    goal=goal,
                    param_names=sorted(params.keys()),
                    perception=perception,
                    history=history,
                )

                if isinstance(outcome, PlannerStuck):
                    final_reason = outcome.reason
                    screenshot = await _capture_screenshot(page, run_dir, "stuck")
                    events.append({"type": "stuck", "reason": outcome.reason, "screenshot": screenshot})
                    break

                if isinstance(outcome, PlannerDone):
                    final_reason = outcome.reason
                    final_checkpoint = outcome.final_checkpoint
                    success = True
                    screenshot = await _capture_screenshot(page, run_dir, "done")
                    events.append({"type": "done", "reason": outcome.reason, "screenshot": screenshot})
                    break

                step: StepSpec = outcome

                # Merge any newly-introduced param bindings, then substitute
                # {{name}} placeholders with real values before executing.
                params.update(step.param_bindings)
                try:
                    resolved_action = step.action.model_copy(
                        update={"value": _substitute(step.action.value, params)}
                    )
                except KeyError as exc:
                    events.append({"type": "step_error", "step_id": step.id, "error": str(exc)})
                    history.append(
                        {"step": step.description, "result": "error", "detail": str(exc)}
                    )
                    continue  # let the planner see this and course-correct or give up

                if step.risky:
                    pending_screenshot = await _capture_screenshot(page, run_dir, f"{step.id}_pending")
                    approved = await confirm_risky(
                        {
                            "step": step.model_dump(),
                            "resolved_action": resolved_action.model_dump(),
                            "screenshot": pending_screenshot,
                        }
                    )
                    if not approved:
                        events.append(
                            {
                                "type": "risky_denied",
                                "step_id": step.id,
                                "description": step.description,
                                "screenshot": pending_screenshot,
                            }
                        )
                        history.append(
                            {
                                "step": step.description,
                                "action_type": step.action.type,
                                "result": "denied",
                                "detail": "a human reviewer denied this risky step",
                            }
                        )
                        continue  # let the planner see the denial and adapt or give up

                result = await surface.act(resolved_action)

                if result.ok and resolved_action.type == "extract" and resolved_action.output_name:
                    outputs[resolved_action.output_name] = result.extracted_text or ""

                screenshot = await _capture_screenshot(page, run_dir, step.id)

                events.append(
                    {
                        "type": "step",
                        "step_id": step.id,
                        "description": step.description,
                        "action_type": step.action.type,
                        "ok": result.ok,
                        "error": result.error,
                        "risky": step.risky,
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