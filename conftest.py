"""Shared pytest fixtures. Living at the repo root also puts the root on
sys.path so `from backend import ...` resolves under `python -m pytest`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend import store
from backend.schema import Capability


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    """Repoint the flat-file store and the runtime-evidence dir at a per-test
    tmp location, so no test can read from or clobber the real artifacts/
    folder. `save()`/`load()`/`list_ids()` read these module globals at call
    time, so a plain setattr is enough."""
    monkeypatch.setattr(store, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(store, "RUNTIME_EVIDENCE_DIR", tmp_path / "runtime_evidence")
    return tmp_path


@pytest.fixture
def make_capability():
    """Factory for a valid Capability; pass field overrides as kwargs."""

    def _make(**overrides) -> Capability:
        base = dict(
            id="cap_abc123def456",
            version=1,
            goal="Create a ParaBank account for a new user",
            target_url="https://parabank.parasoft.com/parabank/register.htm",
            params=[],
            outputs=[],
            steps=[],
            success_checkpoint=None,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        base.update(overrides)
        return Capability(**base)

    return _make
