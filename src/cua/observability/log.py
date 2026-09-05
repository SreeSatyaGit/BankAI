"""
Structured, redacting observability.

Every run (discovery or replay) gets a RunLogger that writes newline-delimited
JSON events plus richer evidence (an HTML/DOM snapshot) on demand. All text is
passed through the redactor before it hits disk, so raw PII/secrets never land
in logs -- a hard requirement for regulated data.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone

from ..safety.policy import redact


class RunLogger:
    def __init__(self, evidence_dir: str, kind: str, run_id: str | None = None):
        self.run_id = run_id or f"{kind}-{uuid.uuid4().hex[:8]}"
        self.kind = kind
        self.dir = os.path.join(evidence_dir, self.run_id)
        os.makedirs(self.dir, exist_ok=True)
        self.log_path = os.path.join(self.dir, "run.jsonl")
        self._fh = open(self.log_path, "w")
        self.event("run_started", kind=kind)

    def event(self, event_name: str, **fields):
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event_name,
        }
        for k, v in fields.items():
            rec[k] = redact(v) if isinstance(v, str) else v
        self._fh.write(json.dumps(rec) + "\n")
        self._fh.flush()
        return rec

    def snapshot(self, label: str, blob: str) -> str:
        """Persist a richer failure/inspection signal (DOM snapshot)."""
        safe = redact(blob)
        path = os.path.join(self.dir, f"{label}-{int(time.time()*1000)}.html")
        with open(path, "w") as f:
            f.write(safe)
        self.event("snapshot", snapshot_label=label,
                   path=os.path.relpath(path, self.dir))
        return path

    def close(self, **summary):
        self.event("run_finished", **summary)
        self._fh.close()
