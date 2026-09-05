"""
The planner seam -- the ONLY place the LLM lives.

A Planner looks at the goal, the declared params, and the current Perception,
and returns the next structured step (or DONE / STUCK). Because it returns fully
formed StepSpecs (semantic locator + fallbacks + checkpoint + risky flag), the
recorder can package a run into an artifact without ever parsing a raw model
transcript, and replay never needs the planner again.

Two implementations:
  * MockPlanner   -- deterministic, offline. Encodes the domain knowledge an LLM
                     would "discover", but still validates each step against the
                     LIVE perception, so discovery genuinely interacts and can
                     detect a stuck state (which drives the escalation demo).
  * ClaudePlanner -- real Anthropic tool-use. Gated behind ANTHROPIC_API_KEY.
                     Uses the exact same StepSpec output contract.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Optional
from pydantic import BaseModel

from ..surface.base import (
    Perception, Action, ActionType, Locator, Strategy,
)
from ..artifact.schema import StepSpec, Checkpoint


class PlanStatus(str, Enum):
    CONTINUE = "continue"
    DONE = "done"
    STUCK = "stuck"


class PlanResult(BaseModel):
    status: PlanStatus
    step: Optional[StepSpec] = None
    reason: Optional[str] = None


class Planner:
    name = "base"

    def next_step(self, goal: str, params: dict, perception: Perception,
                  history: list[StepSpec]) -> PlanResult:
        raise NotImplementedError


# --------------------------------------------------------------------------
# MockPlanner: offline, deterministic domain knowledge, reactive to perception.
# --------------------------------------------------------------------------

def _has_button(p: Perception, name: str) -> bool:
    return name in p.buttons

def _has_link(p: Perception, name: str) -> bool:
    return name in p.links

def _has_field(p: Perception, label: str) -> bool:
    return any(f.label == label for f in p.fields)


class MockPlanner(Planner):
    name = "mock"

    def next_step(self, goal, params, perception, history):
        g = goal.lower()
        done_ids = {s.id for s in history}
        p = perception

        def step(sid, desc, action, checkpoint=None, risky=False):
            return PlanResult(status=PlanStatus.CONTINUE, step=StepSpec(
                id=sid, description=desc, action=action,
                checkpoint=checkpoint, risky=risky))

        # ---- shared: sign on if we're on the login page --------------------
        if _has_field(p, "User ID") and "login" not in done_ids:
            return step("login", "Sign on to the console",
                        Action(type=ActionType.FILL,
                               locator=Locator(strategy=Strategy.LABEL, value="User ID",
                                               description="login username field, "
                                               "identified by its visible label"),
                               value="{{operator_user}}"))
        if "login" in done_ids and "login_pw" not in done_ids and _has_field(p, "Password"):
            return step("login_pw", "Enter password",
                        Action(type=ActionType.FILL,
                               locator=Locator(strategy=Strategy.LABEL, value="Password"),
                               value="{{operator_pass}}"))
        if "login_pw" in done_ids and "login_submit" not in done_ids and _has_button(p, "Sign On"):
            return step("login_submit", "Submit sign-on",
                        Action(type=ActionType.CLICK,
                               locator=Locator(strategy=Strategy.BUTTON, value="Sign On")),
                        risky=False)

        # ---- goal: read savings balance -----------------------------------
        if "savings" in g or "balance" in g:
            if _has_field(p, "Member ID") and "enter_id" not in done_ids:
                return step("enter_id", "Enter the member id to search",
                            Action(type=ActionType.FILL,
                                   locator=Locator(strategy=Strategy.LABEL, value="Member ID"),
                                   value="{{member_id}}"))
            if "enter_id" in done_ids and "do_search" not in done_ids and _has_button(p, "Search"):
                return step("do_search", "Run the member search",
                            Action(type=ActionType.CLICK,
                                   locator=Locator(strategy=Strategy.BUTTON, value="Search")))
            if "do_search" in done_ids and "read_bal" not in done_ids and "Savings" in p.text_digest:
                return step("read_bal", "Read the savings balance from the detail table",
                            Action(type=ActionType.EXTRACT,
                                   locator=Locator(
                                       strategy=Strategy.ROW_VALUE, value="Savings",
                                       description="value cell adjacent to the "
                                       "'Savings' row label in the balances table",
                                       fallbacks=[Locator(strategy=Strategy.CSS,
                                                          value="td.amt")]),
                                   output_name="savings_balance"),
                            checkpoint=Checkpoint(
                                locator=Locator(strategy=Strategy.ROW_VALUE, value="Savings"),
                                description="detail page shows a Savings row"))
            if "read_bal" in done_ids:
                return PlanResult(status=PlanStatus.DONE, reason="balance extracted")

        # ---- goal: open a sub-account to confirmation ---------------------
        if "sub-account" in g or "subaccount" in g or "sub account" in g:
            if _has_field(p, "Member ID") and "enter_id" not in done_ids:
                return step("enter_id", "Enter the member id",
                            Action(type=ActionType.FILL,
                                   locator=Locator(strategy=Strategy.LABEL, value="Member ID"),
                                   value="{{member_id}}"))
            if "enter_id" in done_ids and "do_search" not in done_ids and _has_button(p, "Search"):
                return step("do_search", "Run member search",
                            Action(type=ActionType.CLICK,
                                   locator=Locator(strategy=Strategy.BUTTON, value="Search")))
            if "do_search" in done_ids and "open_form" not in done_ids and _has_link(p, "Open sub-account"):
                return step("open_form", "Open the new sub-account form",
                            Action(type=ActionType.CLICK,
                                   locator=Locator(strategy=Strategy.LINK, value="Open sub-account")))
            if "open_form" in done_ids and "pick_type" not in done_ids and _has_field(p, "Account type"):
                return step("pick_type", "Choose the account type",
                            Action(type=ActionType.SELECT,
                                   locator=Locator(strategy=Strategy.LABEL, value="Account type"),
                                   value="{{account_type}}"))
            if "pick_type" in done_ids and "submit_create" not in done_ids and _has_button(p, "Create"):
                return step("submit_create", "Create the sub-account (irreversible)",
                            Action(type=ActionType.CLICK,
                                   locator=Locator(strategy=Strategy.BUTTON, value="Create")),
                            checkpoint=Checkpoint(
                                locator=Locator(strategy=Strategy.ROW_VALUE, value="Reference"),
                                description="confirmation screen shows a Reference"),
                            risky=True)
            if "submit_create" in done_ids and "read_ref" not in done_ids and "Reference" in p.text_digest:
                return step("read_ref", "Read the confirmation reference",
                            Action(type=ActionType.EXTRACT,
                                   locator=Locator(strategy=Strategy.ROW_VALUE, value="Reference"),
                                   output_name="reference"))
            if "read_ref" in done_ids:
                return PlanResult(status=PlanStatus.DONE, reason="reached confirmation")

        # If we reach here we could not decide a next step from perception.
        return PlanResult(status=PlanStatus.STUCK,
                          reason=f"no known action for state: {p.title!r}")


# --------------------------------------------------------------------------
# ClaudePlanner: real LLM. Same output contract. Not exercised in the offline
# demo, but wired so it works if ANTHROPIC_API_KEY is set.
# --------------------------------------------------------------------------

class ClaudePlanner(Planner):  # pragma: no cover - requires network + key
    name = "claude"

    def __init__(self, model: str = "claude-sonnet-4-6"):
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("set ANTHROPIC_API_KEY to use ClaudePlanner")
        import anthropic  # lazy import
        self.client = anthropic.Anthropic()
        self.model = model

    def next_step(self, goal, params, perception, history):
        """Ask Claude for the next step as a tool call. The tool schema mirrors
        StepSpec, so the returned tool_use input parses straight into a
        StepSpec -- identical contract to MockPlanner."""
        sys = (
            "You operate a legacy back-office web app via a constrained action "
            "API. Given the current page perception and the goal, return the "
            "SINGLE next step, or call finish(status). Prefer semantic locators "
            "(label/button/link/row_value) with fallbacks. Never put raw input "
            "values inline -- reference declared params as {{name}}.")
        tools = [{
            "name": "next_step",
            "description": "Emit the next StepSpec.",
            "input_schema": StepSpec.model_json_schema(),
        }, {
            "name": "finish",
            "description": "Signal the goal is done or the agent is stuck.",
            "input_schema": {"type": "object", "properties": {
                "status": {"enum": ["done", "stuck"]},
                "reason": {"type": "string"}}, "required": ["status"]},
        }]
        msg = self.client.messages.create(
            model=self.model, max_tokens=1024, system=sys, tools=tools,
            messages=[{"role": "user", "content":
                       f"GOAL: {goal}\nPARAMS: {list(params)}\n"
                       f"HISTORY: {[s.id for s in history]}\n"
                       f"PERCEPTION:\n{perception.digest()}"}])
        for block in msg.content:
            if block.type == "tool_use" and block.name == "next_step":
                return PlanResult(status=PlanStatus.CONTINUE,
                                  step=StepSpec.model_validate(block.input))
            if block.type == "tool_use" and block.name == "finish":
                st = block.input.get("status")
                return PlanResult(status=PlanStatus(st),
                                  reason=block.input.get("reason"))
        return PlanResult(status=PlanStatus.STUCK, reason="no tool call returned")


def make_planner(name: str) -> Planner:
    if name == "claude":
        return ClaudePlanner()
    return MockPlanner()
