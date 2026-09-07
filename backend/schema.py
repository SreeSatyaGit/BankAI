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

LocatorStrategy = Literal["label", "role", "placeholder", "text"]
ActionType = Literal["navigate", "click", "type", "select", "extract", "press_enter"]


class Locator(BaseModel):
    """How to find a control on the page. Semantic, not CSS/xpath."""

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
    # For "navigate": a URL. For "type"/"select": text to enter, may reference
    # {{param_name}} placeholders that get substituted from param_bindings/params
    # before execution. Not used for "click".
    value: Optional[str] = None
    # For "extract": the name under which the extracted text is stored in outputs.
    output_name: Optional[str] = None


class StepSpec(BaseModel):
    """One recorded/planned step in a flow."""

    id: str
    description: str
    action: Action
    # Transient: {{param_name}} -> literal value used *this run* to actually drive
    # the page. Stripped before the artifact is persisted (see loop.py).
    param_bindings: Dict[str, str] = Field(default_factory=dict)
    # Human-readable description of the expected resulting state after this step.
    checkpoint: Optional[str] = None
    # Marks the step as risky/irreversible (e.g. submit, delete, transfer).
    risky: bool = False


class PerceptionField(BaseModel):
    label: str
    kind: str  # "text" | "email" | "password" | "textarea" | "select" | "checkbox" | ...
    current_value: Optional[str] = None


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
