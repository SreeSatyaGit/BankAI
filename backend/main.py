"""
FastAPI app. Thin — the real logic lives in loop.py / surface.py / planner.py /
runs.py / replay.py.

POST /api/discover starts a run in the background and returns immediately with a
run_id: discovery can no longer be a single blocking request, because escalating
to a human (a risky step, the planner getting stuck, or a step exhausting its
retries) could pause for an arbitrary amount of time. (The planner API call
itself failing — as opposed to the planner giving up — is NOT escalated to a
human; see loop.py's docstring — that just ends the run.) The frontend polls
GET /api/discover/{run_id} for progress and, when status=="awaiting_intervention",
shows the pending situation and either:
  - resolves it directly via POST /api/discover/{run_id}/resume, or
  - first takes one or more manual actions on the SAME live session via
    POST /api/discover/{run_id}/manual-action, then resumes.

POST /api/replay is a single blocking call (unlike discover) — replay has no
LLM in the loop, so a run either completes or hard-fails in bounded time; there
is no open-ended "waiting on the model" step that would justify the
background-task treatment discover needed. Replay also has no live-pause
capability for the same reason it's a blocking call — see replay.py's docstring.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import runs, store
from .evidence import capture_screenshot
from .loop import run_discovery
from .replay import replay_capability
from .schema import Action, Capability, ReplayResult

app = FastAPI(title="BankAI", version="0.1.0")

store.RUNTIME_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/runtime_evidence",
    StaticFiles(directory=str(store.RUNTIME_EVIDENCE_DIR)),
    name="runtime_evidence",
)

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
    status: str  # "running" | "awaiting_intervention" | "done"
    steps: List[Dict[str, Any]]
    pending_intervention: Optional[Dict[str, Any]] = None
    # Only populated once status == "done":
    ok: Optional[bool] = None
    artifact: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None


class ResumeRequest(BaseModel):
    decision: Literal["approve", "skip", "continue", "retry", "abort"]
    note: Optional[str] = None


class ManualActionRequest(BaseModel):
    action: Action
    description: Optional[str] = None


class ManualActionResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    extracted_text: Optional[str] = None
    screenshot: Optional[str] = None


class ReplayRequest(BaseModel):
    artifact_id: Optional[str] = None
    capability: Optional[Dict[str, Any]] = None
    params: Dict[str, str] = {}
    confirm_risky: bool = False
    auto_fill_missing_params: bool = True


@app.get("/api/health")
async def health() -> Dict[str, bool]:
    return {"ok": True}


async def _execute_run(run: runs.RunState, target_url: str, goal: str) -> None:
    def _on_surface_ready(surface, run_dir) -> None:
        run.live_surface = surface
        run.live_run_dir = run_dir

    try:
        result = await run_discovery(
            target_url=target_url,
            goal=goal,
            run_id=run.run_id,
            events=run.events,
            request_intervention=run.request_intervention,
            on_surface_ready=_on_surface_ready,
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
        run.live_surface = None
        run.live_run_dir = None


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
        "pending_intervention": run.pending_intervention,
    }
    if run.status == "done" and run.result:
        payload["ok"] = run.result.get("ok")
        payload["artifact"] = run.result.get("artifact")
        payload["reason"] = run.result.get("reason")
    return RunStatusResponse(**payload)


@app.post("/api/discover/{run_id}/resume")
async def resume(run_id: str, req: ResumeRequest) -> Dict[str, bool]:
    """Resolves a pending escalation (risky step / stuck / retry-exhausted).
    409 if this run isn't actually paused — most likely you already answered
    it, or it moved on/finished on its own in the meantime."""
    run = runs.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    if run.status != "awaiting_intervention":
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id} has no pending intervention (status={run.status})",
        )
    run.resolve_intervention({"decision": req.decision, "note": req.note})
    return {"ok": True}


@app.post("/api/discover/{run_id}/manual-action", response_model=ManualActionResponse)
async def manual_action(run_id: str, req: ManualActionRequest) -> ManualActionResponse:
    run = runs.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    if run.status != "awaiting_intervention" or run.live_surface is None:
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id} is not currently paused for a human to act on (status={run.status})",
        )

    result = await run.live_surface.act(req.action)

    screenshot = None
    if run.live_run_dir is not None:
        manual_count = sum(1 for e in run.events if e.get("type") == "manual_action") + 1
        screenshot = await capture_screenshot(run.live_surface.page, run.live_run_dir, f"human_{manual_count}")

    run.events.append(
        {
            "type": "manual_action",
            "actor": "human",
            "description": req.description or f"Manual {req.action.type} by human",
            "action_type": req.action.type,
            "ok": result.ok,
            "error": result.error,
            "screenshot": screenshot,
        }
    )

    return ManualActionResponse(ok=result.ok, error=result.error, extracted_text=result.extracted_text, screenshot=screenshot)


@app.get("/api/artifacts")
async def list_artifacts() -> Dict[str, List[str]]:
    return {"artifact_ids": store.list_ids()}


@app.post("/api/replay", response_model=ReplayResult)
async def replay(req: ReplayRequest) -> ReplayResult:
    """
    Deterministic replay: executes a saved Capability's steps directly via
    Surface.act(), with NO LLM in the loop. See replay.py for the full design
    (retry + locator-fallback robustness, business-outcome/checkpoint
    classification, and the risky-step confirm_risky gate).
    """
    if not req.artifact_id and not req.capability:
        raise HTTPException(status_code=400, detail="either artifact_id or capability must be provided")

    if req.capability is not None:
        try:
            capability = Capability.model_validate(req.capability)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid capability payload: {exc}")
    else:
        try:
            capability = store.load(req.artifact_id)  # type: ignore[arg-type]
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown artifact_id: {req.artifact_id}")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"could not load artifact {req.artifact_id}: {exc}")

    return await replay_capability(
        capability=capability,
        params=req.params,
        confirm_risky=req.confirm_risky,
        auto_fill_missing_params=req.auto_fill_missing_params,
    )
