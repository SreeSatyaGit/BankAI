"""Filesystem artifact store. Filename encodes id + version for a minimal
version history (id@version.json)."""
from __future__ import annotations

import os
import glob
from .schema import Capability


class ArtifactStore:
    def __init__(self, root: str = "artifacts"):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, cap_id: str, version: str) -> str:
        return os.path.join(self.root, f"{cap_id}@{version}.json")

    def save(self, cap: Capability) -> str:
        path = self._path(cap.id, cap.version)
        with open(path, "w") as f:
            f.write(cap.to_json())
        return path

    def load(self, cap_id: str, version: str | None = None) -> Capability:
        if version:
            path = self._path(cap_id, version)
        else:
            matches = sorted(glob.glob(os.path.join(self.root, f"{cap_id}@*.json")))
            if not matches:
                raise FileNotFoundError(cap_id)
            path = matches[-1]  # latest by lexical version sort
        with open(path) as f:
            return Capability.from_json(f.read())

    def load_path(self, path: str) -> Capability:
        with open(path) as f:
            return Capability.from_json(f.read())

    def list(self) -> list[str]:
        return sorted(os.path.basename(p) for p in
                      glob.glob(os.path.join(self.root, "*.json")))
