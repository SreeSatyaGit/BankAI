"""
Human-in-the-loop escalation & handoff.

The core idea is a single explicit "who is in control" token over ONE live
session (surface). Automation, when it detects a stuck/blocked state it cannot
safely resolve, does the following:

  1. builds an InterventionRequest carrying enough context to act on
     (capability, goal, current step, current perception, snapshot, reason),
  2. cedes control (controller := HUMAN) and hands the SAME live Surface to an
     Operator -- not a fresh session,
  3. the operator performs manual actions on that surface,
  4. control returns (controller := AUTOMATION) with a decision (resume/abort)
     and a record of exactly what the human did.

The seam that matters: automation must be able to pause, cede, and resume on
the same session. Everything above (surface, artifact, replay) already operates
on a Surface handle, so handing that same handle to the operator IS the
control transfer -- no session migration, no re-login, context preserved.

A real deployment would replace MockOperator with a co-browsing console (e.g.
the surface is a shared browser via CDP/VNC and a human drives it in a UI). The
console is out of scope; the mechanism and control model here are real.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

from ..surface.base import Surface, Action, ActionType, Locator, Strategy
from ..observability.log import RunLogger


class Controller(str, Enum):
    AUTOMATION = "automation"
    HUMAN = "human"


class InterventionRequest(BaseModel):
    run_id: str
    capability_id: str
    goal: str
    step_id: str
    reason: str
    perception_digest: str
    snapshot_ref: Optional[str] = None


class DecisionKind(str, Enum):
    RESUME = "resume"   # human fixed the session; automation should retry/continue
    ABORT = "abort"     # cannot proceed; fail the run cleanly


class OperatorDecision(BaseModel):
    kind: DecisionKind
    note: str = ""
    actions_taken: List[str] = Field(default_factory=list)


class Operator:
    """Something that can take control of a live session and act on it."""
    def handle(self, req: InterventionRequest, surface: Surface,
               log: RunLogger) -> OperatorDecision:
        raise NotImplementedError


class MockOperator(Operator):
    """Stand-in for a human at a co-browsing console.

    It inspects the live surface and performs a minimal, realistic manual
    recovery (here: clearing a supervisor 'maintenance override'), recording
    each manual action. This is scripted, but every action runs against the
    SAME live Surface the automation was using -- the control-transfer is real.
    """
    def handle(self, req, surface, log) -> OperatorDecision:
        actions: List[str] = []
        perception = surface.perceive()

        # Human recognises the maintenance state and applies the override link
        # that the automation was never taught about.
        if "Override (supervisor)" in perception.links:
            surface.act(Action(type=ActionType.CLICK, locator=Locator(
                strategy=Strategy.LINK, value="Override (supervisor)")))
            actions.append("clicked 'Override (supervisor)' to clear the "
                           "maintenance gate")
            log.event("human_action", detail=actions[-1])
            return OperatorDecision(
                kind=DecisionKind.RESUME,
                note="Applied supervisor override; automation may resume.",
                actions_taken=actions)

        # Nothing the human can safely do here.
        return OperatorDecision(
            kind=DecisionKind.ABORT,
            note="No safe manual recovery available for this state.",
            actions_taken=actions)


class HandoffCoordinator:
    """Owns the control token and orchestrates the pause -> cede -> resume seam
    over a single live session."""
    def __init__(self, operator: Operator, log: RunLogger):
        self.operator = operator
        self.log = log
        self.controller = Controller.AUTOMATION

    def escalate(self, req: InterventionRequest, surface: Surface) -> OperatorDecision:
        self.log.event("escalation_raised", step=req.step_id, reason=req.reason,
                       goal=req.goal, capability=req.capability_id)
        self.controller = Controller.HUMAN
        self.log.event("control_transfer", to="human")
        try:
            decision = self.operator.handle(req, surface, self.log)
        finally:
            self.controller = Controller.AUTOMATION
            self.log.event("control_transfer", to="automation")
        self.log.event("escalation_resolved", decision=decision.kind,
                       note=decision.note, actions=decision.actions_taken)
        return decision
