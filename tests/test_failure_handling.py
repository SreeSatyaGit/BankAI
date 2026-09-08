"""How run_discovery surfaces a failed run: ok=False + a reason, no Capability
persisted, and the failure recorded in the events stream. Browser / screenshot /
planner are faked -- no Playwright, Groq or network.
"""

from __future__ import annotations

import asyncio

from backend import loop, store
from backend.planner import PlannerError
from backend.schema import Action, Locator, Perception, StepSpec
from backend.surface import ActionResult


class _FakePage:
    def __init__(self, goto_error=None):
        self._goto_error = goto_error

    async def goto(self, url, timeout=None):
        if self._goto_error:
            raise self._goto_error


class _FakeBrowser:
    def __init__(self, goto_error=None):
        self._goto_error = goto_error

    async def new_page(self):
        return _FakePage(self._goto_error)

    async def close(self):
        return None


class _FakePlaywrightCM:
    def __init__(self, goto_error=None):
        self._goto_error = goto_error

    async def __aenter__(self):
        goto_error = self._goto_error

        class _Chromium:
            async def launch(self, headless=True):
                return _FakeBrowser(goto_error)

        return type("_PW", (), {"chromium": _Chromium()})()

    async def __aexit__(self, *exc):
        return False


def _fake_surface_factory(act_ok):
    class _S:
        def __init__(self, page):
            pass

        async def perceive(self):
            return Perception(url="https://x/register", title="Register")

        async def act(self, action):
            return ActionResult(act_ok, error=None if act_ok else "element not found")

    return _S


async def _fake_capture(page, run_dir, name):
    return f"/runtime_evidence/x/{name}.png"


class _ScriptedPlanner:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)

    def next_step(self, **kwargs):
        return self._outcomes.pop(0)


def _click_step():
    return StepSpec(
        id="step_1",
        description="Click Register",
        action=Action(type="click", locator=Locator(strategy="role", role="button", value="Register")),
    )


def _run(monkeypatch, outcomes, *, goto_error=None, act_ok=True):
    monkeypatch.setattr(loop, "async_playwright", lambda: _FakePlaywrightCM(goto_error))
    monkeypatch.setattr(loop, "Surface", _fake_surface_factory(act_ok))
    monkeypatch.setattr(loop, "capture_screenshot", _fake_capture)
    monkeypatch.setattr(loop, "RETRY_BACKOFF_MS", 0)  # don't actually sleep between retries
    return asyncio.run(
        loop.run_discovery("https://x/register", "Create an account", planner=_ScriptedPlanner(outcomes))
    )


def _artifact_files():
    return sorted(p.name for p in store.ARTIFACTS_DIR.glob("*.json"))


def test_target_url_that_wont_load_fails_cleanly(monkeypatch):
    result = _run(monkeypatch, [], goto_error=RuntimeError("net::ERR_NAME_NOT_RESOLVED"))
    assert result["ok"] is False
    assert result["reason"].startswith("could not load target_url:")
    assert result["artifact"] is None
    assert result["steps"] == []
    assert _artifact_files() == []


def test_planner_error_is_terminal_and_recorded(monkeypatch):
    result = _run(monkeypatch, [PlannerError(reason="planner API call failed twice in a row: boom")])
    assert result["ok"] is False
    assert result["reason"] == "planner API call failed twice in a row: boom"
    assert result["artifact"] is None
    planner_error_events = [e for e in result["steps"] if e["type"] == "planner_error"]
    assert len(planner_error_events) == 1
    assert planner_error_events[0]["screenshot"] == "/runtime_evidence/x/planner_error.png"
    assert _artifact_files() == []


def test_step_failure_escalates_then_aborts_with_no_human(monkeypatch):
    # act() always fails -> mechanical retries exhausted -> retry_exhausted
    # escalation -> default handler (no human wired) aborts the run.
    result = _run(monkeypatch, [_click_step()], act_ok=False)
    assert result["ok"] is False
    # The run ends on the abort decision's note...
    assert result["reason"] == "no intervention handler wired up"
    # ...but the escalation event carries the real cause for a human to see.
    (requested,) = [e for e in result["steps"] if e["type"] == "intervention_requested"]
    assert requested["trigger"] == "retry_exhausted"
    assert "step_1 failed after 3 attempt(s): element not found" in requested["reason"]
    assert _artifact_files() == []
