"""
Shared screenshot-evidence capture, used by both loop.py (discovery) and
replay.py (replay). Screenshots are written under RUNTIME_EVIDENCE_DIR and
served back at /runtime_evidence/<run_dir_name>/<name>.png (see main.py's
static mount) — this is per-run DEBUG evidence, distinct from the assignment's
own curated /evidence/ submission folder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


async def capture_screenshot(page, run_dir: Path, name: str) -> Optional[str]:
    """Best-effort screenshot. Never fails the caller — a screenshot that
    couldn't be taken (e.g. page mid-navigation) just means no visual for that
    moment, not a broken run."""
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"{name}.png"
        await page.screenshot(path=str(path), timeout=5000)
        return f"/runtime_evidence/{run_dir.name}/{name}.png"
    except Exception:  # noqa: BLE001
        return None
