"""
The Surface seam.

Everything above this layer (agent loop, artifact, replay) speaks in terms of
*intent* -- "fill the field labelled 'Member ID'", "click the 'Search' button",
"read the value in the 'Savings' row" -- never in terms of a concrete DOM,
screenshot, or accessibility node.

A Surface is anything that can (a) PERCEIVE its current state as a structured
Perception and (b) ACT on a Locator. That is the whole contract. An HTTP/HTML
surface, a Playwright/browser surface, an accessibility-tree surface, or a
screenshot+coordinates CUA all implement the same interface, so the artifact
schema and the replay engine are surface-agnostic.

Locators are intentionally *semantic* (label text, role, visible text, table
structure) rather than physical (CSS path, pixel coords). Semantic locators are
what survive across tenants running the same vendor product with different
branding, and what a human reviewer can actually read.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class Strategy(str, Enum):
    LABEL = "label"            # form field by its adjacent/visible label text
    NAME = "name"             # form field by its name attribute (fallback)
    LINK = "link"             # anchor by visible text
    BUTTON = "button"         # submit/button by value or text
    ROW_VALUE = "row_value"   # table cell value keyed by an adjacent label cell
    TEXT_CONTAINS = "text_contains"  # any element containing text (checkpoints)
    CSS = "css"               # escape hatch; least portable


class Locator(BaseModel):
    """How to find a control/element, with an ordered fallback chain.

    Replay tries `primary`, then each fallback in order, and records which one
    resolved. Fallbacks are how we degrade gracefully across tenant variants
    without re-recording the whole flow.
    """
    strategy: Strategy
    value: str
    fallbacks: List["Locator"] = Field(default_factory=list)
    description: Optional[str] = None  # human-readable reasoning about robustness

    def chain(self) -> List["Locator"]:
        return [self] + list(self.fallbacks)


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    FILL = "fill"
    SELECT = "select"
    CLICK = "click"
    EXTRACT = "extract"       # read a value out of the page (no state change)
    WAIT_FOR = "wait_for"     # assert/await a condition (checkpoint primitive)


class Action(BaseModel):
    type: ActionType
    locator: Optional[Locator] = None
    value: Optional[str] = None       # url for navigate; text for fill/select
    output_name: Optional[str] = None  # for EXTRACT: name to bind the result to


class FieldInfo(BaseModel):
    label: Optional[str] = None
    name: Optional[str] = None
    kind: str = "text"  # text | password | select | submit


class Perception(BaseModel):
    """A structured, surface-independent snapshot of the current state.

    This is what the agent's planner reasons over and what we log as evidence.
    It is deliberately compact and redaction-friendly.
    """
    url: str
    title: str
    status: int = 200
    fields: List[FieldInfo] = Field(default_factory=list)
    links: List[str] = Field(default_factory=list)
    buttons: List[str] = Field(default_factory=list)
    text_digest: str = ""  # short visible-text summary

    def digest(self) -> str:
        parts = [f"[{self.status}] {self.title} ({self.url})"]
        if self.fields:
            parts.append("fields: " + ", ".join(
                f"{f.label or f.name}:{f.kind}" for f in self.fields))
        if self.buttons:
            parts.append("buttons: " + ", ".join(self.buttons))
        if self.links:
            parts.append("links: " + ", ".join(self.links))
        if self.text_digest:
            parts.append("text: " + self.text_digest)
        return "\n".join(parts)


class ActResult(BaseModel):
    ok: bool
    resolved_by: Optional[Strategy] = None  # which locator in the chain worked
    extracted: Optional[str] = None
    error: Optional[str] = None


class LocatorNotFound(Exception):
    def __init__(self, locator: Locator):
        self.locator = locator
        super().__init__(f"could not resolve locator: {locator.strategy}={locator.value!r}")


class Surface:
    """Abstract surface. Implementations must be deterministic given the same
    server state; no model is ever consulted at this layer."""

    def perceive(self) -> Perception:
        raise NotImplementedError

    def act(self, action: Action) -> ActResult:
        raise NotImplementedError

    def snapshot(self) -> str:
        """Return a raw evidence blob (HTML / screenshot ref / a11y dump)."""
        raise NotImplementedError

    def close(self) -> None:
        pass


Locator.model_rebuild()
