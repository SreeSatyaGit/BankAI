"""
The planner seam -- the ONLY place the LLM lives.

A Planner looks at the goal, the declared params, and the current Perception,
and returns the next structured step (or DONE / STUCK). Because it returns fully
formed StepSpecs (semantic locator + fallbacks + checkpoint + risky flag), the
recorder can package a run into an artifact without ever parsing a raw model
transcript, and replay never needs the planner again.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Optional
from pydantic import BaseModel

from ..surface.base import Perception
from ..artifact.schema import StepSpec


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


class GroqPlanner(Planner):
    """Real LLM via Groq's OpenAI-compatible tool-use API. Gated behind
    GROQ_API_KEY. Uses the same StepSpec output contract as any planner."""

    name = "groq"

    def __init__(self, model: str = "openai/gpt-oss-20b"):
        from groq import Groq  # lazy import
        self.client = Groq()  # reads GROQ_API_KEY from env
        self.model = model

    def next_step(self, goal, params, perception, history):
        sys = (
            "You operate a legacy back-office web app via a constrained action "
            "API. Given the current page perception and the goal, return the "
            "SINGLE next step, or call finish(status). Prefer semantic locators "
            "(label/button/link/row_value) with fallbacks. Never put raw input "
            "values inline -- reference declared params as {{name}}.")
        tools = [{
            "type": "function",
            "function": {
                "name": "next_step",
                "description": "Emit the next StepSpec.",
                "parameters": StepSpec.model_json_schema(),
            },
        }, {
            "type": "function",
            "function": {
                "name": "finish",
                "description": "Signal the goal is done or the agent is stuck.",
                "parameters": {"type": "object", "properties": {
                    "status": {"enum": ["done", "stuck"]},
                    "reason": {"type": "string"}}, "required": ["status"]},
            },
        }]
        messages = [
            {"role": "system", "content": sys},
            {"role": "user", "content":
             f"GOAL: {goal}\nPARAMS: {list(params)}\n"
             f"HISTORY: {[s.id for s in history]}\n"
             f"PERCEPTION:\n{perception.digest()}"},
        ]

        for attempt in range(2):
            resp = self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tools, tool_choice="auto")
            msg = resp.choices[0].message
            calls = msg.tool_calls or []
            if not calls:
                messages.append({"role": "user", "content":
                                  "You must call next_step or finish. Try again."})
                continue
            call = calls[0]
            try:
                args = json.loads(call.function.arguments)
                if call.function.name == "finish":
                    return PlanResult(status=PlanStatus(args["status"]),
                                      reason=args.get("reason"))
                return PlanResult(status=PlanStatus.CONTINUE,
                                  step=StepSpec.model_validate(args))
            except Exception as e:
                messages.append({"role": "user", "content":
                                  f"That call failed to parse ({e}). "
                                  f"Re-emit valid JSON matching the schema."})

        return PlanResult(status=PlanStatus.STUCK,
                          reason="planner did not return a valid tool call after retry")


def make_planner(name: str) -> Planner:
    return GroqPlanner()