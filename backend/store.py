"""
Flat-file artifact store. One JSON file per Capability under artifacts/.
Deliberately dumb — no DB, no locking — this is a skeleton. Swapping this module
for a real store later shouldn't require touching loop.py or main.py.

Filenames embed a slug of the user-entered goal for at-a-glance browsing:
`<goal-slug>__<capability_id>.json` (e.g.
`create-a-parabank-account__cap_3a50c7c78d4f.json`). The capability id after the
`__` stays the stable key everything else addresses artifacts by — `load()` and
`list_ids()` resolve/extract it regardless of the slug, and files written before
this change (bare `<capability_id>.json`) still load.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

from .schema import Capability

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"
RUNTIME_EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "runtime_evidence"

_SLUG_SEPARATOR = "__"
_MAX_SLUG_LEN = 60


def _slugify(text: str) -> str:
    """Lowercase, keep [a-z0-9], collapse every other run to a single '-',
    trim leading/trailing '-', and cap the length so filenames stay sane."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if len(slug) > _MAX_SLUG_LEN:
        slug = slug[:_MAX_SLUG_LEN].rstrip("-")
    return slug


def _id_from_stem(stem: str) -> str:
    """The capability id is whatever follows the last '__' (or the whole stem,
    for legacy bare-id files)."""
    return stem.rsplit(_SLUG_SEPARATOR, 1)[-1]


def _find_path(artifact_id: str) -> Optional[Path]:
    """Locate the file for a capability id: preferred slugged form
    `*__<id>.json`, then the legacy bare `<id>.json`."""
    if not ARTIFACTS_DIR.exists():
        return None
    for path in ARTIFACTS_DIR.glob(f"*{_SLUG_SEPARATOR}{artifact_id}.json"):
        return path
    legacy = ARTIFACTS_DIR / f"{artifact_id}.json"
    return legacy if legacy.exists() else None


def _path_for(artifact_id: str, goal: str = "") -> Path:
    slug = _slugify(goal)
    name = f"{slug}{_SLUG_SEPARATOR}{artifact_id}.json" if slug else f"{artifact_id}.json"
    return ARTIFACTS_DIR / name


def save(capability: Capability) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    existing = _find_path(capability.id)
    path = _path_for(capability.id, capability.goal)
    if existing and existing != path:
        existing.unlink()
    path.write_text(capability.model_dump_json(indent=2), encoding="utf-8")
    return path


def load(artifact_id: str) -> Capability:
    path = _find_path(artifact_id)
    if path is None:
        raise FileNotFoundError(f"no artifact file for id {artifact_id!r}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return Capability.model_validate(data)


def list_ids() -> List[str]:
    if not ARTIFACTS_DIR.exists():
        return []
    return sorted(_id_from_stem(p.stem) for p in ARTIFACTS_DIR.glob("*.json"))
