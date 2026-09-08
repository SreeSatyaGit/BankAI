"""
Deterministic replay engine — runs a saved Capability WITHOUT the LLM in the
loop. This is the production execution path an AI agent would trigger: given
an artifact and typed params, drive the same steps recorded during discovery
using Surface.act() directly, and return a structured result that separates
success, a known business outcome, a blocked (unconfirmed risky) step, and a
hard failure. planner.py is never imported here.

Robustness model (deliberately two separate mechanisms for two separate
failure modes — see REPORT.md's "Determinism & error handling" once written):
  - STRUCTURAL: StepSpec.action.locator.fallbacks — a different way to find
    the same element if the primary strategy stops resolving (e.g. the label
    text changed but the HTML name attribute didn't). Lives in surface.py.
  - TEMPORAL: per-step retry, below (MAX_ATTEMPTS). The element is findable
    and correct, but the page hadn't finished an animation/XHR/transition yet.
A step that still fails after both is a HARD FAILURE — replay stops
immediately rather than continuing into a page state the recorded steps never
anticipated ("respond deliberately rather than blindly proceeding").

Outcome classification, checked in this priority order after each step:
  1. business_outcome — Capability.known_outcomes is an optional, human-
     curated {name: substring} map, checked against the page (title + url +
     digest) after every step that completes without a hard failure. This is
     the honest, deterministic way to recognize an expected business result
     (e.g. "no such member") without an LLM to interpret free text. It is
     empty by default — discovery has no reliable way to populate it in
     advance, so today it's something a reviewer adds after inspecting a
     saved artifact. See Cuts in REPORT.md.
  2. success — every step completed, no known_outcomes pattern matched.
     success_checkpoint (free text from the planner) is checked with a
     best-effort keyword-overlap heuristic ONLY and surfaced as
     `checkpoint_verified` — this is a signal for a human/caller to weigh, not
     proof. True verification of a free-text checkpoint without an LLM is an
     open problem.

Risky steps: replay does NOT execute a risky=True step unless the caller
passes confirm_risky=True. Otherwise replay stops right before it and reports
status="blocked" with exactly which step needs sign-off. A capability being
saved (i.e. approved once, with specific params, during discovery) is not the
same as every future replay invocation — with different params — being safe by
default (e.g. a different transfer amount is a materially different action),
so this is intentionally a conscious per-call opt-in rather than inherited
trust from discovery-time approval.

Params: any param the caller doesn't explicitly supply is auto-filled with a
fabricated, plausible-looking test value (see defaults.py) unless
auto_fill_missing_params=False. These are generated from the param NAME
alone — never derived from any real prior run — and every value actually
used (supplied or generated) is reported back via
ReplayResult.used_params/auto_filled_params, so nothing is a silent swap.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from playwright.async_api import async_playwright

from . import store
from .defaults import generate_default
from .evidence import capture_screenshot
from .schema import Capability, ReplayResult, ReplayStepResult
from .surface import Surface
from .templating import substitute

MAX_ATTEMPTS = 3  # 1 initial try + up to 2 retries, per spec
RETRY_BACKOFF_MS = 400  # linear backoff between attempts on the same step
HEADLESS = os.environ.get("BANKAI_HEADLESS", "true").lower() != "false"
_CHECKPOINT_OVERLAP_THRESHOLD = 0.4
_STOPWORDS = {
    "the", "a", "an", "to", "of", "and", "on", "in", "is", "was", "were",
    "page", "shows", "show", "shown", "field", "with", "that", "this",
    "from", "into", "your", "you", "has", "have", "been",
}


def _keywords(text: str) -> Set[str]:
    return {
        w
        for w in re.findall(r"[a-zA-Z0-9]+", text.lower())
        if len(w) > 3 and w not in _STOPWORDS
    }


def _checkpoint_overlap(checkpoint: Optional[str], title: str, url: str, digest: str) -> Optional[bool]:
    """Best-effort, NOT proof. Returns None if there's no checkpoint text (or
    no extractable keywords) to check at all."""
    if not checkpoint:
        return None
    keywords = _keywords(checkpoint)
    if not keywords:
        return None
    haystack = f"{title} {url} {digest}".lower()
    hits = sum(1 for kw in keywords if kw in haystack)
    return (hits / len(keywords)) >= _CHECKPOINT_OVERLAP_THRESHOLD


def _matches_known_outcome(known_outcomes: Dict[str, str], title: str, url: str, digest: str) -> Optional[str]:
    """Returns the matched outcome name, or None if nothing matched."""
    haystack = f"{title} {url} {digest}".lower()
    for name, pattern in known_outcomes.items():
        if pattern and pattern.lower() in haystack:
            return name
    return None


async def replay_capability(
    capability: Capability,
    params: Dict[str, str],
    run_id: Optional[str] = None,
    confirm_risky: bool = False,
    auto_fill_missing_params: bool = True,
) -> ReplayResult:
    run_id = run_id or f"replay_{uuid.uuid4().hex[:12]}"
    run_dir = store.RUNTIME_EVIDENCE_DIR / f"replay_{run_id}"
    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()

    def _finish(status: str, **kwargs) -> ReplayResult:
        return ReplayResult(
            status=status,
            artifact_id=capability.id,
            run_id=run_id,
            duration_ms=int((time.monotonic() - t0) * 1000),
            started_at=started_at.isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            **kwargs,
        )

    used_params: Dict[str, str] = dict(params)
    auto_filled_params: List[str] = []
    if auto_fill_missing_params:
        for name in capability.params:
            if not used_params.get(name):
                used_params[name] = generate_default(name)
                auto_filled_params.append(name)

    missing = [p for p in capability.params if p not in used_params]
    if missing:
        return _finish(
            "failed",
            steps=[],
            outputs={},
            used_params=used_params,
            auto_filled_params=auto_filled_params,
            checkpoint_verified=None,
            error=f"missing required param(s): {', '.join(missing)}",
        )

    step_results: List[ReplayStepResult] = []
    outputs: Dict[str, str] = {}
    status = "success"
    outcome_name: Optional[str] = None
    top_error: Optional[str] = None
    checkpoint_verified: Optional[bool] = None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS)
        page = await browser.new_page()
        surface = Surface(page)

        try:
            await page.goto(capability.target_url, timeout=30000)
        except Exception as exc:  # noqa: BLE001
            await browser.close()
            return _finish(
                "failed",
                steps=[],
                outputs={},
                used_params=used_params,
                auto_filled_params=auto_filled_params,
                checkpoint_verified=None,
                error=f"could not load target_url: {exc}",
            )

        try:
            for step in capability.steps:
                step_t0 = time.monotonic()

                try:
                    resolved_action = step.action.model_copy(
                        update={"value": substitute(step.action.value, used_params)}
                    )
                except KeyError as exc:
                    step_results.append(
                        ReplayStepResult(
                            step_id=step.id,
                            description=step.description,
                            action_type=step.action.type,
                            ok=False,
                            attempts=0,
                            error=str(exc),
                            screenshot=None,
                            duration_ms=int((time.monotonic() - step_t0) * 1000),
                        )
                    )
                    status = "failed"
                    top_error = f"step {step.id}: {exc}"
                    break

                if step.risky and not confirm_risky:
                    pending_screenshot = await capture_screenshot(page, run_dir, f"{step.id}_blocked")
                    status = "blocked"
                    top_error = (
                        f"step {step.id} ({step.description}) is marked risky and "
                        "confirm_risky was not set — replay stopped before executing it"
                    )
                    step_results.append(
                        ReplayStepResult(
                            step_id=step.id,
                            description=step.description,
                            action_type=step.action.type,
                            ok=False,
                            attempts=0,
                            error="blocked: risky step requires confirm_risky=true",
                            screenshot=pending_screenshot,
                            duration_ms=int((time.monotonic() - step_t0) * 1000),
                        )
                    )
                    break

                result = None
                attempts = 0
                for attempt in range(1, MAX_ATTEMPTS + 1):
                    attempts = attempt
                    result = await surface.act(resolved_action)
                    if result.ok:
                        break
                    if attempt < MAX_ATTEMPTS:
                        await asyncio.sleep(RETRY_BACKOFF_MS / 1000 * attempt)

                screenshot = await capture_screenshot(page, run_dir, step.id)

                if result.ok and resolved_action.type == "extract" and resolved_action.output_name:
                    outputs[resolved_action.output_name] = result.extracted_text or ""

                step_results.append(
                    ReplayStepResult(
                        step_id=step.id,
                        description=step.description,
                        action_type=step.action.type,
                        ok=result.ok,
                        attempts=attempts,
                        error=result.error,
                        screenshot=screenshot,
                        duration_ms=int((time.monotonic() - step_t0) * 1000),
                    )
                )

                if not result.ok:
                    status = "failed"
                    top_error = (
                        f"step {step.id} ({step.description}) failed after "
                        f"{attempts} attempt(s): {result.error}"
                    )
                    break  # hard failure: stop immediately

                if capability.known_outcomes:
                    perception = await surface.perceive()
                    matched = _matches_known_outcome(
                        capability.known_outcomes, perception.title, perception.url, perception.digest
                    )
                    if matched:
                        status = "business_outcome"
                        outcome_name = matched
                        break

            if status == "success":
                final_perception = await surface.perceive()
                checkpoint_verified = _checkpoint_overlap(
                    capability.success_checkpoint,
                    final_perception.title,
                    final_perception.url,
                    final_perception.digest,
                )
        finally:
            await browser.close()

    return _finish(
        status,
        outcome_name=outcome_name,
        steps=step_results,
        outputs=outputs,
        used_params=used_params,
        auto_filled_params=auto_filled_params,
        checkpoint_verified=checkpoint_verified,
        error=top_error,
    )
