"""
Typed contracts for BankAI. Pydantic models only — no behavior lives here.

Design notes:
- Locator is SEMANTIC (label / role / placeholder / visible text), never CSS/xpath,
  because the target surfaces we ultimately care about (legacy web, desktop) don't
  offer stable selectors. A `fallbacks` chain lets a step degrade gracefully if the
  primary locator strategy stops resolving.
- Action.value and StepSpec.param_bindings work together so that raw, possibly
  sensitive values (a member id, an amount typed into a form) are NEVER frozen into
  the persisted artifact. During discovery, param_bindings holds {name: literal}
  for any {{name}} the planner introduces this step; loop.py uses it to actually
  type something into the page, then strips it before the StepSpec is persisted.
  Only the *names* survive into Capability.params.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

LocatorStrategy = Literal["label", "role", "placeholder", "text", "name"]
ActionType = Literal["navigate", "click", "type", "select", "extract", "press_enter"]


class Locator(BaseModel):
    """How to find a control on the page.

    Prefers real semantic signals (label / role+name / placeholder / visible
    text), but also supports "name" — matching an element's HTML `name`
    attribute. This isn't a CSS/xpath positional hack: it's a stable,
    developer-assigned identifier, and it's often the ONLY stable hook legacy
    server-rendered forms expose (see surface.py's perceive(), which reports
    exactly which strategy will work for each field instead of guessing).
    """

    strategy: LocatorStrategy
    value: str
    # Only meaningful when strategy == "role" (e.g. role="button", value=accessible name)
    role: Optional[str] = None
    fallbacks: List["Locator"] = Field(default_factory=list)


Locator.model_rebuild()


class Action(BaseModel):
    """A single act() call against the Surface."""

    type: ActionType
    locator: Optional[Locator] = None
    value: Optional[str] = None
    output_name: Optional[str] = None


class StepSpec(BaseModel):
    """One recorded/planned step in a flow."""

    id: str
    description: str
    action: Action
    param_bindings: Dict[str, str] = Field(default_factory=dict)
    checkpoint: Optional[str] = None
    risky: bool = False


class PerceptionField(BaseModel):
    label: str
    kind: str  # "text" | "email" | "password" | "textarea" | "select" | "checkbox" | ...
    current_value: Optional[str] = None
    locator_strategy: LocatorStrategy = "label"


class PerceptionControl(BaseModel):
    name: str  # accessible name / visible text
    role: str  # "button" | "link"


class Perception(BaseModel):
    """What the agent currently sees on the page."""

    url: str
    title: str
    fields: List[PerceptionField] = Field(default_factory=list)
    buttons: List[PerceptionControl] = Field(default_factory=list)
    links: List[PerceptionControl] = Field(default_factory=list)
    # Short, truncated text summary of page content, for the planner's context.
    digest: str = ""


class Capability(BaseModel):
    """
    The persisted, reusable artifact. This is the contract an AI agent (or a human
    reviewer) reads to understand what the capability does, what it needs, and what
    it returns. Deliberately decoupled from the raw model transcript.
    """

    id: str
    version: int = 1
    goal: str
    target_url: str
    params: List[str] = Field(default_factory=list)  # names only, never values
    outputs: List[str] = Field(default_factory=list)  # names only
    steps: List[StepSpec] = Field(default_factory=list)
    success_checkpoint: Optional[str] = None
    created_at: str
    # Optional, human-curated: {outcome_name: substring_to_match}. Checked
    # against the page (title + url + digest) after every replayed step —
    # if a pattern matches, replay stops and reports that named business
    # outcome instead of blindly treating it as success or failure. Empty by
    # default; discovery doesn't populate this automatically (see replay.py
    # and REPORT.md's Cuts section) — it's something a reviewer adds after
    # inspecting a saved artifact, e.g. {"member_not_found": "No member found"}.
    known_outcomes: Dict[str, str] = Field(default_factory=dict)


class ReplayStepResult(BaseModel):
    """Outcome of executing one StepSpec during replay."""

    step_id: str
    description: str
    action_type: ActionType
    ok: bool
    attempts: int  # 1 if it succeeded first try; up to MAX_ATTEMPTS if retried
    error: Optional[str] = None
    screenshot: Optional[str] = None
    duration_ms: int


ReplayStatus = Literal["success", "business_outcome", "blocked", "failed"]


class ReplayResult(BaseModel):
    """
    Structured result of a replay run — the contract the calling agent (or a
    human) reads to know what happened. Deliberately separates:
      - "success": every step executed, no known business outcome matched
      - "business_outcome": a Capability.known_outcomes pattern matched partway
        through — a legitimate answer, not a crash (e.g. "no such member")
      - "blocked": stopped before executing a risky step because the caller
        didn't pass confirm_risky=true
      - "failed": a step exhausted its retries/fallbacks — a hard failure
    """

    status: ReplayStatus
    outcome_name: Optional[str] = None  # populated when status == "business_outcome"
    artifact_id: str
    run_id: str
    steps: List[ReplayStepResult] = Field(default_factory=list)
    outputs: Dict[str, str] = Field(default_factory=dict)
    # Every param actually used to run this replay — caller-supplied values
    # plus any that were auto-generated to fill a gap (see defaults.py).
    # Transparent by design: nothing here is a silent substitution.
    used_params: Dict[str, str] = Field(default_factory=dict)
    # Which of the names in used_params were fabricated rather than supplied.
    auto_filled_params: List[str] = Field(default_factory=list)
    # Best-effort keyword-overlap check of success_checkpoint against the final
    # page. None if there was no checkpoint to check (or replay didn't reach
    # the end). NOT proof of correctness — see replay.py's docstring.
    checkpoint_verified: Optional[bool] = None
    error: Optional[str] = None  # top-level detail when status == "failed" or "blocked"
    duration_ms: int
    started_at: str
    finished_at: str
