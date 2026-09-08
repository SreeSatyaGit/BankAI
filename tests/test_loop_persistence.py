"""run_discovery persist-on-success: a successful run writes exactly one
Capability artifact (filename from the goal) with param_bindings stripped; a run
that doesn't succeed writes nothing. Browser, screenshotter and planner are all
faked -- no Playwright, Groq or network.
"""

from __future__ import annotations

import asyncio

from backend import loop, store
from backend.planner import PlannerDone, PlannerStuck
from backend.schema import Action, Locator, Perception, StepSpec
from backend.surface import ActionResult


class _FakePage:
    async def goto(self, url, timeout=None):
        return None


class _FakeBrowser:
    async def new_page(self):
        return _FakePage()

    async def close(self):
        return None


class _FakeChromium:
    async def launch(self, headless=True):
        return _FakeBrowser()


class _FakePlaywright:
    chromium = _FakeChromium()


class _FakePlaywrightCM:
    async def __aenter__(self):
        return _FakePlaywright()

    async def __aexit__(self, *exc):
        return False


def _fake_surface(page):
    class _S:
        async def perceive(self):
            return Perception(url="https://x/register", title="Register")

        async def act(self, action):
            return ActionResult(True)

    return _S()


async def _fake_capture(page, run_dir, name):
    return f"/runtime_evidence/x/{name}.png"


class _ScriptedPlanner:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)

    def next_step(self, **kwargs):
        return self._outcomes.pop(0)


def _type_step():
    return StepSpec(
        id="step_1",
        description="Enter first name",
        action=Action(
            type="type",
            locator=Locator(strategy="label", value="First name"),
            value="{{first_name}}",
        ),
        param_bindings={"first_name": "John"},
    )


def _run(monkeypatch, outcomes, goal="Create a ParaBank account for a new user"):
    monkeypatch.setattr(loop, "async_playwright", lambda: _FakePlaywrightCM())
    monkeypatch.setattr(loop, "Surface", _fake_surface)
    monkeypatch.setattr(loop, "capture_screenshot", _fake_capture)
    return asyncio.run(
        loop.run_discovery("https://x/register", goal, planner=_ScriptedPlanner(outcomes))
    )


def _artifact_files():
    return sorted(p.name for p in store.ARTIFACTS_DIR.glob("*.json"))


def test_success_persists_artifact_named_from_goal(monkeypatch):
    result = _run(monkeypatch, [_type_step(), PlannerDone(reason="done", final_checkpoint="Welcome John")])
    assert result["ok"] is True
    artifact_id = result["artifact"]["id"]
    assert _artifact_files() == [f"create-a-parabank-account-for-a-new-user__{artifact_id}.json"]
    assert store.load(artifact_id).goal == "Create a ParaBank account for a new user"


def test_persisted_step_has_no_param_bindings(monkeypatch):
    result = _run(monkeypatch, [_type_step(), PlannerDone(reason="done", final_checkpoint=None)])
    reloaded = store.load(result["artifact"]["id"])
    assert reloaded.steps[0].param_bindings == {}
    assert reloaded.params == ["first_name"]  # only the name survives


def test_unsuccessful_run_persists_nothing(monkeypatch):
    result = _run(monkeypatch, [PlannerStuck(reason="cloudflare wall")])
    assert result["ok"] is False
    assert _artifact_files() == []
