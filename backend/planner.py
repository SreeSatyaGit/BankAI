"""
GroqPlanner is the single place the LLM lives in this system. Nothing else in the
codebase imports `groq` or knows a model name. loop.py hands it a goal + the current
Perception + history and gets back either a next StepSpec to execute, or a signal
that the run is done or stuck.

The model is driven with tool/function-calling and exactly two tools:
  - next_step: emit one StepSpec to execute now.
  - finish:    declare the goal done, or declare the run stuck.

The model is explicitly instructed to never inline literal values it needs to type
(a member id, an amount, a search term mentioned in the goal) directly into
action.value. Instead it must invent a short param name, put "{{name}}" in
action.value, and provide the literal under param_bindings — that's what lets
loop.py execute this run correctly while keeping the eventual persisted artifact
free of raw data.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from groq import Groq

from .schema import Action, Perception, StepSpec

MODEL_NAME = "openai/gpt-oss-20b"

# The Groq key can be provided under either name, and either a `.env` next to this
# file (backend/.env) or the project-root `.env` (the one .env.example lives next
# to) — checked in that order, then falling back to real process env vars.
_ENV_CANDIDATE_PATHS = (
    os.path.join(os.path.dirname(__file__), ".env"),  # backend/.env
    os.path.join(os.path.dirname(__file__), "..", ".env"),  # bankai/.env (repo root)
)
_API_KEY_VAR_NAMES = ("bank_api_interface", "GROQ_API_KEY")


def _load_env_file(path: str) -> Dict[str, str]:
    """Minimal `.env` reader: `KEY = value` / `KEY=value`, `#` comments, and
    optional surrounding single or double quotes on the value. No dependency on
    python-dotenv."""
    values: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, raw = line.partition("=")
        name = name.strip()
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        if name:
            values[name] = raw
    return values


def _resolve_api_key() -> Optional[str]:
    # 1. Any `.env` file, backend/.env checked before the repo-root .env.
    for path in _ENV_CANDIDATE_PATHS:
        file_values = _load_env_file(path)
        for var_name in _API_KEY_VAR_NAMES:
            if file_values.get(var_name):
                return file_values[var_name]
    # 2. Real process environment (e.g. `export GROQ_API_KEY=...`).
    for var_name in _API_KEY_VAR_NAMES:
        if os.environ.get(var_name):
            return os.environ[var_name]
    return None


_STEP_SPEC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "short unique id for this step, e.g. 'step_3'"},
        "description": {"type": "string", "description": "what this step does, for a human reviewer"},
        "action": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["navigate", "click", "type", "select", "extract", "press_enter"],
                },
                "locator": {
                    "type": ["object", "null"],
                    "description": "omit for 'navigate'; required for click/type/select/extract/press_enter",
                    "properties": {
                        "strategy": {"type": "string", "enum": ["label", "role", "placeholder", "text", "name"]},
                        "value": {
                            "type": "string",
                            "description": "label text / accessible name / placeholder text / visible text",
                        },
                        "role": {
                            "type": ["string", "null"],
                            "description": "only when strategy=='role', e.g. 'button', 'link', 'textbox'",
                        },
                    },
                    "required": ["strategy", "value"],
                },
                "value": {
                    "type": ["string", "null"],
                    "description": (
                        "for 'navigate': a URL. For 'type'/'select': text to enter — NEVER a raw "
                        "literal, always '{{param_name}}' referencing a name declared in "
                        "param_bindings on this same step. Omit for 'click'/'extract'."
                    ),
                },
                "output_name": {
                    "type": ["string", "null"],
                    "description": "only for 'extract': the name to store the extracted text under",
                },
            },
            "required": ["type"],
        },
        "param_bindings": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": (
                "Only when action.value introduces a NEW {{name}}: map that name to the literal "
                "value to actually type this run, e.g. {'member_id': '12345'}. Omit/empty otherwise."
            ),
        },
        "checkpoint": {
            "type": ["string", "null"],
            "description": "expected observable state after this step succeeds",
        },
        "risky": {
            "type": "boolean",
            "description": "true if this step is irreversible or high-impact (submit, delete, transfer, confirm)",
        },
    },
    "required": ["id", "description", "action"],
}

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "next_step",
            "description": "Emit exactly one step to execute next against the live page.",
            "parameters": _STEP_SPEC_SCHEMA,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Declare the run finished: either the goal was achieved, or the agent is stuck.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["done", "stuck"]},
                    "reason": {"type": "string", "description": "why done, or why stuck"},
                    "final_checkpoint": {
                        "type": ["string", "null"],
                        "description": "only if status=='done': observable condition confirming success",
                    },
                },
                "required": ["status", "reason"],
            },
        },
    },
]

_SYSTEM_PROMPT = """You are a computer-use agent that operates a real, live web page \
one step at a time to accomplish a user's goal. You are told the current goal, the \
params discovered so far, the current page perception (url, title, visible fields, \
buttons, links, and a short text digest), and the history of steps already taken \
this run.

Rules:
- Call exactly one tool per turn: `next_step` or `finish`.
- Use ONLY semantic locators (label / role+name / placeholder / visible text / name) \
that match something actually present in the current perception. Never invent a \
control that isn't listed.
- Each field in current_perception.fields has its own "locator_strategy" — this \
tells you EXACTLY which Locator.strategy to use for that field. Always copy it \
directly rather than guessing "label". Many legacy forms have no real label at \
all, and perceive() reports "name" for those — using "label" against such a \
field will never resolve, no matter how many times you retry.
- Entries in current_perception.buttons and current_perception.links are ALWAYS \
targeted with strategy="role" — role="button" for buttons, role="link" for \
links — and value equal to that entry's "name". NEVER use strategy="name" for a \
button or link: "name" only applies to a FIELD whose own locator_strategy says \
"name" (it refers to that input's HTML name attribute, which most buttons don't \
even have).
- NEVER put a literal value you need to type directly in action.value. Instead \
invent a short snake_case param name, write "{{that_name}}" in action.value, and \
record the literal under param_bindings on the SAME step, e.g. \
action.value="{{member_id}}", param_bindings={"member_id": "12345"}. Reuse the same \
name if you're filling the same logical value again later.
- If a field or control you need is not present in the current perception, do not \
guess — either try a reasonable prior step (e.g. a search/navigate) or call finish \
with status="stuck" and explain what was missing.
- Call finish(status="done") only once the page state clearly shows the goal was \
achieved, and describe that observable state in final_checkpoint.
- Mark risky=true on any step that submits, deletes, transfers, or otherwise takes \
an irreversible/high-impact action.
- Keep step descriptions short and concrete.

The `locator` object ALWAYS has exactly this shape — "strategy" and "value" are \
both REQUIRED keys, spelled exactly like this, never anything else:
  {"strategy": "label", "value": "First name"}
  {"strategy": "role", "value": "Submit", "role": "button"}
  {"strategy": "placeholder", "value": "Search..."}
  {"strategy": "text", "value": "View details"}
  {"strategy": "name", "value": "customer.firstName"}
Do NOT write {"label": "..."} or any other key name — "strategy"/"value" are the \
only correct keys for locator.

Example of a fully correct next_step call, given a visible field labeled "First name":
{
  "id": "step_1",
  "description": "Enter first name",
  "action": {
    "type": "type",
    "locator": {"strategy": "label", "value": "First name"},
    "value": "{{first_name}}"
  },
  "param_bindings": {"first_name": "John"},
  "checkpoint": "first name field shows John",
  "risky": false
}

Example of a fully correct next_step call clicking a button from
current_perception.buttons (note strategy="role", never "name", for buttons):
{
  "id": "step_12",
  "description": "Click Register button to submit the form",
  "action": {
    "type": "click",
    "locator": {"strategy": "role", "role": "button", "value": "Register"}
  },
  "checkpoint": "page navigates away from the registration form to an account confirmation page",
  "risky": true
}
"""


@dataclass
class PlannerStuck:
    reason: str


@dataclass
class PlannerDone:
    reason: str
    final_checkpoint: Optional[str]


PlannerOutcome = Any  # StepSpec | PlannerDone | PlannerStuck


class GroqPlanner:
    """The only class in this codebase that talks to an LLM."""

    def __init__(self, api_key: Optional[str] = None, model: str = MODEL_NAME):
        key = api_key or _resolve_api_key()
        if not key:
            raise RuntimeError(
                "No Groq API key found. Set `bank_api_interface` (or `GROQ_API_KEY`) "
                "in a .env file (backend/.env or the repo-root bankai/.env), or export "
                "it as a real environment variable, before starting the backend."
            )
        self._client = Groq(api_key=key)
        self._model = model

    def next_step(
        self,
        goal: str,
        param_names: List[str],
        perception: Perception,
        history: List[Dict[str, Any]],
    ) -> PlannerOutcome:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": goal,
                        "params_declared_so_far": param_names,
                        "current_perception": perception.model_dump(),
                        "history": history,
                    }
                ),
            },
        ]
        return self._call_with_one_retry(messages)

    def _call_with_one_retry(self, messages: List[Dict[str, Any]]) -> PlannerOutcome:
        outcome = self._call_once(messages)
        if outcome is not None:
            return outcome
        # Malformed output — retry exactly once, telling the model what went wrong.
        messages = messages + [
            {
                "role": "user",
                "content": (
                    "Your previous response could not be parsed into a valid tool call. "
                    "A common mistake: the `locator` object must have exactly the keys "
                    "'strategy' and 'value' (e.g. {\"strategy\": \"label\", \"value\": \"First name\"}) "
                    "— not '{\"label\": \"...\"}' or any other shape. "
                    "Call exactly one of `next_step` or `finish` with valid arguments."
                ),
            }
        ]
        outcome = self._call_once(messages)
        if outcome is not None:
            return outcome
        return PlannerStuck(reason="planner returned malformed output twice in a row")

    def _call_once(self, messages: List[Dict[str, Any]]) -> Optional[PlannerOutcome]:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=_TOOLS,
                tool_choice="required",
                temperature=0.2,
            )
        except Exception as exc:  # noqa: BLE001 — treat any API failure as "malformed" for retry purposes
            print(f"[planner] Groq API call failed: {exc}")
            return None

        choice = response.choices[0]
        tool_calls = choice.message.tool_calls or []
        if not tool_calls:
            return None

        call = tool_calls[0]
        try:
            args = json.loads(call.function.arguments)
        except json.JSONDecodeError:
            return None

        if call.function.name == "finish":
            status = args.get("status")
            reason = args.get("reason", "")
            if status == "done":
                return PlannerDone(reason=reason, final_checkpoint=args.get("final_checkpoint"))
            if status == "stuck":
                return PlannerStuck(reason=reason)
            return None

        if call.function.name == "next_step":
            try:
                action = Action.model_validate(args.get("action", {}))
                step = StepSpec(
                    id=args["id"],
                    description=args["description"],
                    action=action,
                    param_bindings=args.get("param_bindings") or {},
                    checkpoint=args.get("checkpoint"),
                    risky=bool(args.get("risky", False)),
                )
            except Exception as exc:  # noqa: BLE001 — validation failure -> malformed, trigger retry
                print(f"[planner] StepSpec validation failed: {exc}")
                return None
            return step

        return None