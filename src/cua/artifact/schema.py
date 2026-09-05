"""
The capability artifact schema.

An artifact is a *capability contract*, not a macro. It is designed to be read
by two audiences at once:

  - a calling AI agent, which needs a typed signature (params in, outputs out)
    to invoke it like a function, and
  - a human reviewer, who needs to see what it does, how each control is
    located, what counts as success, and how known failures are classified.

Key design choices (defended in REPORT.md > Artifact schema):

  * Locators are semantic (see surface.base) and carry a fallback chain, so the
    same artifact tolerates cosmetic differences across tenant variants.
  * The error taxonomy is DATA, not code: every artifact declares its own
    `outcomes` -- business outcomes, recoverable conditions, and hard failures.
    Replay is a generic interpreter of this taxonomy, so new capabilities need
    no new replay code.
  * Values that come from the caller are templated as {{param}}. The recorder
    replaces any concrete input that matches a declared param, so no captured
    input value (which could be PII) is ever frozen into the artifact.
  * `provenance` links back to the discovery run but the artifact is otherwise
    fully decoupled from the raw model transcript.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from ..surface.base import Locator, Action

SCHEMA_VERSION = "1.0"


class ParamType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"


class ParamSpec(BaseModel):
    name: str
    type: ParamType = ParamType.STRING
    required: bool = True
    pattern: Optional[str] = None  # regex the value must match
    description: Optional[str] = None
    sensitive: bool = False        # if true, never logged in the clear


class OutputSpec(BaseModel):
    name: str
    type: ParamType = ParamType.STRING
    description: Optional[str] = None
    sensitive: bool = False


class Checkpoint(BaseModel):
    """An assertion that we actually reached the expected state."""
    locator: Locator
    description: Optional[str] = None


class StepSpec(BaseModel):
    id: str
    description: str
    action: Action
    checkpoint: Optional[Checkpoint] = None  # verify after the action
    risky: bool = False                       # irreversible / state-changing
    max_retries: int = 0
    retry_on: List[str] = Field(default_factory=list)  # outcome names


class OutcomeKind(str, Enum):
    BUSINESS = "business"      # legitimate result the caller must know about
    RECOVERABLE = "recoverable"  # handle and continue (dismiss / retry / wait)
    HARD = "hard"             # stop and surface a debuggable failure


class RecoverKind(str, Enum):
    RETRY = "retry"           # re-run the current step
    DISMISS = "dismiss"       # click a locator (e.g. an interstitial) then continue
    WAIT = "wait"             # pause and re-check


class Match(BaseModel):
    """How to detect an outcome from a Perception/ActResult."""
    status_in: List[int] = Field(default_factory=list)
    text_contains: Optional[str] = None
    locator_present: Optional[Locator] = None


class RecoverAction(BaseModel):
    kind: RecoverKind
    locator: Optional[Locator] = None
    max_attempts: int = 1
    wait_seconds: float = 0.5


class OutcomeDetector(BaseModel):
    name: str
    kind: OutcomeKind
    when: Match
    message: Optional[str] = None
    recover: Optional[RecoverAction] = None  # required iff kind == RECOVERABLE
    escalate: bool = False                    # HARD outcomes may request a human


class TargetSpec(BaseModel):
    app_id: str                # logical app (vendor product), not a tenant URL
    entry: str                 # entry route/URL, may be templated
    surface_kind: str = "http_html"  # http_html | playwright | a11y | cua


class TenantBinding(BaseModel):
    """Design hook for multi-tenant reuse (see REPORT.md). A base capability is
    recorded once against the vendor product; per-tenant bindings supply the
    concrete base_url and any locator/route overrides."""
    tenant_id: str
    base_url: str
    overrides: Dict[str, Locator] = Field(default_factory=dict)  # step_id -> locator


class Provenance(BaseModel):
    discovery_run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    planner: str = "unknown"   # e.g. "claude-3.7" or "mock"


class Capability(BaseModel):
    schema_version: str = SCHEMA_VERSION
    id: str                    # stable capability id, e.g. "read_savings_balance"
    version: str = "1.0.0"     # semantic version of THIS capability
    name: str
    description: str
    goal: str                  # the natural-language goal it was discovered from
    target: TargetSpec
    params: List[ParamSpec] = Field(default_factory=list)
    outputs: List[OutputSpec] = Field(default_factory=list)
    steps: List[StepSpec]
    success: Checkpoint
    outcomes: List[OutcomeDetector] = Field(default_factory=list)
    provenance: Optional[Provenance] = None
    approval: str = "draft"    # draft | approved (gates unattended replay)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, s: str) -> "Capability":
        return cls.model_validate_json(s)
