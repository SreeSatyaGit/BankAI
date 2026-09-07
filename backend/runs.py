"""
In-memory registry of discovery runs.

This exists to solve one specific problem: POST /api/discover used to block for
the whole run and return once at the end. That's incompatible with pausing
mid-run to ask a human to approve a risky step — an HTTP request can't sit open
indefinitely waiting on a person. So discovery now runs as a background asyncio
task, tracked here by run_id; the frontend polls GET /api/discover/{run_id} for
progress, and if the run is paused on a risky step, answers via
POST /api/discover/{run_id}/confirm.

RunState.request_confirmation() is the actual pause/resume primitive: loop.py
awaits it before executing any risky step, and it doesn't return until
POST /api/discover/{run_id}/confirm sets a decision and wakes the asyncio.Event.
This is deliberately the same shape a full live-session handoff (spec 3.6) would
build on — "pause automation, let a human act, signal resume" — just scoped down
to "approve/deny one step" rather than a full manual takeover of the page.

Deliberately NOT persistent: a process-local dict is the honestly-scoped choice
for a skeleton (see REPORT.md). It doesn't survive a restart and doesn't work
across multiple backend processes/workers — fine for `uvicorn ... ` with the
default single worker, not fine as-is for a real deployment.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

RunStatus = str  # "running" | "awaiting_confirmation" | "done"


@dataclass
class RunState:
    run_id: str
    status: RunStatus = "running"
    events: List[Dict[str, Any]] = field(default_factory=list)
    # Set while status == "awaiting_confirmation": the step + resolved action a
    # human needs to approve or deny, for display in the UI. Never persisted.
    pending_step: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    task: Optional[asyncio.Task] = None

    _confirmation_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _confirmation_decision: Optional[bool] = field(default=None, repr=False)

    async def request_confirmation(self, payload: Dict[str, Any]) -> bool:
        """Pause here until a human answers via resolve_confirmation(). Only one
        confirmation is ever pending at a time per run (the loop is sequential),
        so a single reusable Event is enough."""
        self.pending_step = payload
        self.status = "awaiting_confirmation"
        self._confirmation_decision = None
        self._confirmation_event.clear()
        await self._confirmation_event.wait()
        self.status = "running"
        self.pending_step = None
        return bool(self._confirmation_decision)

    def resolve_confirmation(self, approve: bool) -> None:
        self._confirmation_decision = approve
        self._confirmation_event.set()


RUNS: Dict[str, RunState] = {}


def create_run(run_id: str) -> RunState:
    run = RunState(run_id=run_id)
    RUNS[run_id] = run
    return run


def get_run(run_id: str) -> Optional[RunState]:
    return RUNS.get(run_id)
