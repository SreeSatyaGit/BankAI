"""
Safety guardrails: allowlist, risky-action handling, redaction.

Scope note (REPORT.md > Safety): this is a policy *decision point*, not a
sandbox. It constrains what the agent/replay are permitted to attempt; it does
not by itself prevent a compromised surface from doing harm. Its limits are
documented in the report.
"""
from __future__ import annotations
from typing import List, Tuple

import re
from urllib.parse import urlparse
from pydantic import BaseModel, Field

from ..surface.base import Action, ActionType


class Policy(BaseModel):
    allowed_hosts: List[str] = Field(default_factory=list)   # e.g. ["127.0.0.1:5001"]
    allowed_path_prefixes: List[str] = Field(default_factory=lambda: ["/"])
    allowed_actions: List[ActionType] = Field(
        default_factory=lambda: list(ActionType))
    # action types considered irreversible / state-changing by default:
    risky_actions: List[ActionType] = Field(default_factory=lambda: [ActionType.CLICK])
    # in unattended mode, risky steps are blocked unless explicitly confirmed:
    require_confirmation_for_risky: bool = True

    def allows_navigation(self, url: str) -> Tuple[bool, str]:
        p = urlparse(url if "://" in url else "http://" + url)
        host = p.netloc or ""
        if self.allowed_hosts and host and host not in self.allowed_hosts:
            return False, f"host {host!r} not on allowlist"
        path = p.path or "/"
        if not any(path.startswith(pre) for pre in self.allowed_path_prefixes):
            return False, f"path {path!r} not on allowlist"
        return True, "ok"

    def allows_action(self, action: Action) -> Tuple[bool, str]:
        if action.type not in self.allowed_actions:
            return False, f"action {action.type} not permitted"
        if action.type == ActionType.NAVIGATE and action.value:
            return self.allows_navigation(action.value)
        return True, "ok"


# ---- redaction -----------------------------------------------------------

_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[REDACTED-CARD]"),
    (re.compile(r"(?i)(password|passwd|secret|token|apikey|api_key)"
                r"\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[REDACTED-EMAIL]"),
]


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def redact_value(name: str, value: str, sensitive: bool) -> str:
    """Redact a named value. Explicitly-sensitive params/outputs are masked
    entirely; everything else is scrubbed for obvious secrets/PII patterns."""
    if sensitive:
        return "[REDACTED]"
    return redact(value)
