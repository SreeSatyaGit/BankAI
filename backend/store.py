"""
Flat-file artifact store. One JSON file per Capability under artifacts/.
Deliberately dumb — no DB, no locking — this is a skeleton. Swapping this module
for a real store later shouldn't require touching loop.py or main.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .schema import Capability

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"


def _path_for(artifact_id: str) -> Path:
    return ARTIFACTS_DIR / f"{artifact_id}.json"


def save(capability: Capability) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for(capability.id)
    path.write_text(capability.model_dump_json(indent=2), encoding="utf-8")
    return path


def load(artifact_id: str) -> Capability:
    path = _path_for(artifact_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    return Capability.model_validate(data)


def list_ids() -> List[str]:
    if not ARTIFACTS_DIR.exists():
        return []
    return sorted(p.stem for p in ARTIFACTS_DIR.glob("*.json"))
