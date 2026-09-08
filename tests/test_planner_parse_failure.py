"""planner.py handling of Groq's HTTP 400 `output_parse_failed` (model replied
with reasoning text instead of a tool call): it must be treated as malformed
output -- corrective retry, then PlannerStuck -- NOT as terminal PlannerError.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
from groq import BadRequestError

from backend.planner import GroqPlanner, PlannerError, PlannerStuck
from backend.schema import StepSpec


def _bad_request(code):
    body = {"error": {"message": code, "type": "invalid_request_error", "code": code}}
    request = httpx.Request("POST", "https://api.groq.com/v1/chat/completions")
    return BadRequestError(code, response=httpx.Response(400, request=request, json=body), body=body)


def _tool_call_response(name, arguments):
    fn = SimpleNamespace(name=name, arguments=json.dumps(arguments))
    message = SimpleNamespace(tool_calls=[SimpleNamespace(function=fn)])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeCompletions:
    def __init__(self, behaviours):
        self._behaviours = list(behaviours)

    def create(self, **kwargs):
        behaviour = self._behaviours.pop(0)
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour


def _planner(behaviours):
    planner = GroqPlanner.__new__(GroqPlanner)  # skip __init__ / API-key resolution
    planner._client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(behaviours)))
    planner._model = "test-model"
    return planner


def _call(planner):
    return planner._call_with_one_retry([{"role": "user", "content": "{}"}])


def test_output_parse_failed_routes_to_stuck_not_error():
    outcome = _call(_planner([_bad_request("output_parse_failed"), _bad_request("output_parse_failed")]))
    assert isinstance(outcome, PlannerStuck)


def test_genuine_bad_request_routes_to_planner_error():
    outcome = _call(_planner([_bad_request("model_not_found"), _bad_request("model_not_found")]))
    assert isinstance(outcome, PlannerError)


def test_corrective_retry_after_parse_failure_can_recover():
    planner = _planner(
        [
            _bad_request("output_parse_failed"),
            _tool_call_response(
                "next_step",
                {"id": "step_1", "description": "go", "action": {"type": "navigate", "value": "https://x"}},
            ),
        ]
    )
    outcome = _call(planner)
    assert isinstance(outcome, StepSpec)
    assert outcome.id == "step_1"
