"""
In-memory registry of discovery runs.

This exists to solve one specific problem: POST /api/discover used to block for
the whole run and return once at the end. That's incompatible with pausing
mid-run to escalate to a human — an HTTP request can't sit open indefinitely
waiting on a person. So discovery runs as a background asyncio task, tracked
here by run_id; the frontend polls GET /api/discover/{run_id} for progress and,
when paused, resolves the escalation via POST /api/discover/{run_id}/resume —
or acts directly on the SAME live session via
POST /api/discover/{run_id}/manual-action before resuming.

RunState.request_intervention() is the actual pause/resume primitive: loop.py
awaits it whenever it needs to escalate — a risky step, the planner getting
stuck, or a step that exhausted its retries — and it doesn't return until
POST /api/discover/{run_id}/resume sets a decision and wakes the asyncio.Event.
(The planner API call itself failing is NOT routed through this — that ends
the run automatically, no human prompt: an API failure isn't something a
human can fix by acting on the page.)

live_surface / live_run_dir are what make this a genuine "take control of the
SAME live session" handoff rather than just a yes/no vote: while a run is
paused, main.py's manual-action endpoint calls live_surface.act(...) directly
against the exact Playwright page the agent was mid-flow on. This is safe
without any locking because asyncio is cooperative and single-threaded here —
run_discovery() is genuinely suspended (awaiting the Event) while the manual-
action handler runs, so nothing else touches the page concurrently.

Deliberately NOT persistent: a process-local dict is the honestly-scoped choice
for a skeleton (see REPORT.md). It doesn't survive a restart and doesn't work
across multiple backend processes/workers — fine for `uvicorn ...` with the
default single worker, not fine as-is for a real deployment.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

RunStatus = str  # "running" | "awaiting_intervention" | "done"


@dataclass
class RunState:
    run_id: str
    status: RunStatus = "running"
    events: List[Dict[str, Any]] = field(default_factory=list)
    # Set while status == "awaiting_intervention": trigger/reason/step/
    # perception/screenshot for display in the UI. Never persisted.
    pending_intervention: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    task: Optional[asyncio.Task] = None

    live_surface: Optional[Any] = None  
    live_run_dir: Optional[Any] = None  

    _intervention_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _intervention_decision: Optional[Dict[str, Any]] = field(default=None, repr=False)

    async def request_intervention(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Pause here until a human resolves it via resolve_intervention().
        Returns the decision dict, e.g. {"decision": "approve"} or
        {"decision": "abort", "note": "..."}. Only one intervention is ever
        pending at a time per run (the loop is sequential), so a single
        reusable Event is enough."""
        self.pending_intervention = payload
        self.status = "awaiting_intervention"
        self._intervention_decision = None
        self._intervention_event.clear()
        await self._intervention_event.wait()
        self.status = "running"
        self.pending_intervention = None
        return self._intervention_decision or {"decision": "abort"}

    def resolve_intervention(self, decision: Dict[str, Any]) -> None:
        self._intervention_decision = decision
        self._intervention_event.set()


RUNS: Dict[str, RunState] = {}


def create_run(run_id: str) -> RunState:
    run = RunState(run_id=run_id)
    RUNS[run_id] = run
    return run


def get_run(run_id: str) -> Optional[RunState]:
    return RUNS.get(run_id)
