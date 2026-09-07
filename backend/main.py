"""
FastAPI app. Thin — the real logic lives in loop.py / surface.py / planner.py /
runs.py.

POST /api/discover starts a run in the background and returns immediately with a
run_id: discovery can no longer be a single blocking request, because a risky
step needs to be able to pause mid-run and wait on a human's answer, which could
take an arbitrary amount of time. The frontend polls GET /api/discover/{run_id}
for progress and, when status=="awaiting_confirmation", shows the pending step
and answers via POST /api/discover/{run_id}/confirm.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import runs, store
from .loop import run_discovery

app = FastAPI(title="BankAI", version="0.1.0")

# Serves the per-run screenshots loop.py writes to runtime_evidence/<run_id>/*.png
# so the frontend (or you, directly) can view them at /runtime_evidence/<...>.png.
store.RUNTIME_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/runtime_evidence",
    StaticFiles(directory=str(store.RUNTIME_EVIDENCE_DIR)),
    name="runtime_evidence",
)

# Skeleton-friendly CORS: the Vite dev server proxies /api anyway, but allow direct
# cross-origin calls too so the frontend can be pointed at the backend directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class DiscoverRequest(BaseModel):
    target_url: str
    goal: str


class DiscoverStartResponse(BaseModel):
    run_id: str
    status: str


class RunStatusResponse(BaseModel):
    run_id: str
    status: str  # "running" | "awaiting_confirmation" | "done"
    steps: List[Dict[str, Any]]
    pending_step: Optional[Dict[str, Any]] = None
    # Only populated once status == "done":
    ok: Optional[bool] = None
    artifact: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None


class ConfirmRequest(BaseModel):
    approve: bool


class ReplayRequest(BaseModel):
    artifact_id: str
    params: Dict[str, str] = {}


@app.get("/api/health")
async def health() -> Dict[str, bool]:
    return {"ok": True}


async def _execute_run(run: runs.RunState, target_url: str, goal: str) -> None:
    try:
        result = await run_discovery(
            target_url=target_url,
            goal=goal,
            run_id=run.run_id,
            events=run.events,
            confirm_risky=run.request_confirmation,
        )
        run.result = result
    except Exception as exc:  # noqa: BLE001 — never leave a run stuck "running" forever
        run.result = {
            "ok": False,
            "steps": run.events,
            "artifact": None,
            "reason": f"internal error: {exc}",
            "run_id": run.run_id,
        }
    finally:
        run.status = "done"


@app.post("/api/discover", response_model=DiscoverStartResponse)
async def discover(req: DiscoverRequest) -> DiscoverStartResponse:
    """Starts a real, LLM-driven discovery loop against the live target_url in
    the background and returns immediately. Poll GET /api/discover/{run_id} for
    progress. No canned responses — if the LLM can't complete the goal, the run
    ends with ok=False and an honest reason."""
    if not req.target_url.strip() or not req.goal.strip():
        raise HTTPException(status_code=400, detail="target_url and goal are both required")
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    run = runs.create_run(run_id)
    run.task = asyncio.create_task(_execute_run(run, req.target_url, req.goal))
    return DiscoverStartResponse(run_id=run_id, status=run.status)


@app.get("/api/discover/{run_id}", response_model=RunStatusResponse)
async def discover_status(run_id: str) -> RunStatusResponse:
    run = runs.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    payload: Dict[str, Any] = {
        "run_id": run.run_id,
        "status": run.status,
        "steps": run.events,
        "pending_step": run.pending_step,
    }
    if run.status == "done" and run.result:
        payload["ok"] = run.result.get("ok")
        payload["artifact"] = run.result.get("artifact")
        payload["reason"] = run.result.get("reason")
    return RunStatusResponse(**payload)


@app.post("/api/discover/{run_id}/confirm")
async def confirm_step(run_id: str, req: ConfirmRequest) -> Dict[str, bool]:
    """Answers a pending risky-step confirmation. 409 if this run isn't
    actually paused waiting on one — most likely you already answered it, or
    the run moved on/finished on its own (e.g. max-steps) in the meantime."""
    run = runs.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    if run.status != "awaiting_confirmation":
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id} has no pending confirmation (status={run.status})",
        )
    run.resolve_confirmation(req.approve)
    return {"ok": True}


@app.get("/api/artifacts")
async def list_artifacts() -> Dict[str, List[str]]:
    return {"artifact_ids": store.list_ids()}


@app.post("/api/replay")
async def replay(req: ReplayRequest) -> None:
    """
    STUB — replay is the next milestone, intentionally not implemented here.

    This skeleton only builds the discovery path: goal -> LLM-driven live run ->
    persisted Capability artifact. Deterministic replay (loading a Capability,
    substituting req.params into each step's {{name}} placeholders, driving the
    Surface WITHOUT the planner in the loop, verifying success_checkpoint, and
    returning a structured result that distinguishes success / known business
    outcome / hard failure) is real work that deserves its own pass rather than a
    faked response here. See REPORT.md's "Determinism & error handling" section
    for the intended design once it's built.
    """
    raise HTTPException(
        status_code=501,
        detail="replay is not implemented yet — this is the next milestone, see comment in main.py",
    )