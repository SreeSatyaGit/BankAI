"""
FastAPI app. Thin — the real logic lives in loop.py / surface.py / planner.py.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import store
from .loop import run_discovery

app = FastAPI(title="BankAI", version="0.1.0")

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


class DiscoverResponse(BaseModel):
    ok: bool
    steps: List[Dict[str, Any]]
    artifact: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None


class ReplayRequest(BaseModel):
    artifact_id: str
    params: Dict[str, str] = {}


@app.get("/api/health")
async def health() -> Dict[str, bool]:
    return {"ok": True}


@app.post("/api/discover", response_model=DiscoverResponse)
async def discover(req: DiscoverRequest) -> DiscoverResponse:
    """Runs a real, LLM-driven discovery loop against the live target_url. No
    canned responses — if the LLM can't complete the goal, this returns ok=False
    with an honest reason."""
    if not req.target_url.strip() or not req.goal.strip():
        raise HTTPException(status_code=400, detail="target_url and goal are both required")
    result = await run_discovery(target_url=req.target_url, goal=req.goal)
    return DiscoverResponse(**result)


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
