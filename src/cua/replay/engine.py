"""
Deterministic replay -- the production execution path. No planner/LLM is ever
consulted here; every decision comes from the artifact.

The result contract is deliberately TRI-STATE (the most common design mistake
in this space is conflating a business outcome with a crash):

  SUCCESS          -- reached the success checkpoint; returns typed outputs.
  BUSINESS_OUTCOME -- a legitimate result the caller must handle
                      (e.g. "no such member"); NOT a failure.
  FAILURE          -- a hard failure, with step / expected / observed / evidence.

Runtime conditions are classified by the artifact's declarative `outcomes`:
  BUSINESS     -> stop, return BUSINESS_OUTCOME (clean).
  RECOVERABLE  -> dismiss / retry / wait (bounded), then continue.
  HARD         -> escalate to a human if configured, else FAILURE.

An unexpected state not covered by the taxonomy (e.g. a failed checkpoint with
no matching detector) also escalates when an operator is configured, and only
becomes a FAILURE if no human can resolve it.
"""
from __future__ import annotations

import re
import time
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from ..surface.base import Surface, Action, ActionType
from ..safety.policy import Policy, redact_value
from ..observability.log import RunLogger
from ..artifact.schema import (
    Capability, StepSpec, Checkpoint, OutcomeDetector, OutcomeKind, Match,
    RecoverKind, ParamSpec,
)
from ..agent.templating import substitute
from ..escalation.handoff import (
    HandoffCoordinator, InterventionRequest, DecisionKind,
)


class ReplayStatus(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    FAILURE = "failure"


class ReplayResult(BaseModel):
    status: ReplayStatus
    run_id: str
    outputs: Dict[str, str] = Field(default_factory=dict)   # raw, for the caller
    outcome_name: Optional[str] = None                       # for BUSINESS_OUTCOME
    message: Optional[str] = None
    failed_step: Optional[str] = None                        # for FAILURE
    expected: Optional[str] = None
    observed: Optional[str] = None
    evidence_ref: Optional[str] = None


class _StepFlow(str, Enum):
    PROCEED = "proceed"
    EARLY = "early"   # a ReplayResult was produced; return it now


class Replayer:
    def __init__(self, surface: Surface, policy: Policy, logger: RunLogger,
                 coordinator: Optional[HandoffCoordinator] = None,
                 confirm_risky: bool = False, allow_draft: bool = False):
        self.surface = surface
        self.policy = policy
        self.log = logger
        self.coordinator = coordinator
        self.confirm_risky = confirm_risky
        self.allow_draft = allow_draft
        self.outputs: Dict[str, str] = {}
        self._last_action: Optional[Action] = None
        self._early: Optional[ReplayResult] = None

    # ---- public ----------------------------------------------------------
    def replay(self, cap: Capability, params: dict) -> ReplayResult:
        self.log.event("replay_started", cap_id=cap.id, version=cap.version,
                       approval=cap.approval)

        # approval gate for unattended replay
        if cap.approval != "approved" and not self.allow_draft:
            return self._fail(None, "capability not approved for unattended "
                              "replay (use --allow-draft to override)", "")

        err = self._validate_params(cap.params, params)
        if err:
            return self._fail(None, f"invalid params: {err}", "")

        entry = substitute(cap.target.entry, params)
        ok, why = self.policy.allows_navigation(entry)
        if not ok:
            return self._fail(None, f"entry navigation blocked: {why}", "")
        self._act(Action(type=ActionType.NAVIGATE, value=entry))

        # some outcomes can appear right at entry (e.g. transient load error)
        if self._handle_state(cap, step=None) is _StepFlow.EARLY:
            return self._early

        for step in cap.steps:
            flow = self._run_step(cap, step, params)
            if flow is _StepFlow.EARLY:
                return self._early

        # final success verification
        if not self._verify(cap.success):
            return self._escalate_or_fail(
                cap, None, "success checkpoint not satisfied",
                expected=cap.success.description or str(cap.success.locator.value))

        red = {k: self._redact_output(cap, k, v) for k, v in self.outputs.items()}
        self.log.event("replay_success", outputs=red)
        self.log.close(status="success")
        return ReplayResult(status=ReplayStatus.SUCCESS, run_id=self.log.run_id,
                            outputs=self.outputs, message="goal reached")

    # ---- per-step --------------------------------------------------------
    def _run_step(self, cap: Capability, step: StepSpec, params: dict) -> _StepFlow:
        allowed, why = self.policy.allows_action(step.action)
        if not allowed:
            self._early = self._fail(step.id, f"policy blocked: {why}", "")
            return _StepFlow.EARLY

        if step.risky and not self.confirm_risky:
            self.log.event("risky_blocked", step=step.id)
            self._early = self._fail(
                step.id, "risky/irreversible step requires confirmation "
                "(--confirm-risky) or human approval", observed="")
            return _StepFlow.EARLY

        exec_action = step.action.model_copy(deep=True)
        exec_action.value = substitute(step.action.value, params)
        res = self._act(exec_action)
        self.log.event("act", step=step.id, action=step.action.type,
                       value=step.action.value, ok=res.ok,
                       resolved_by=res.resolved_by, error=res.error)
        self._capture_output(step, res)

        # classify the resulting state against the artifact taxonomy
        if self._handle_state(cap, step) is _StepFlow.EARLY:
            return _StepFlow.EARLY

        # if the raw action failed and no taxonomy entry explained it -> escalate/fail
        if not res.ok:
            self._early = self._escalate_or_fail(
                cap, step, f"action failed: {res.error}",
                expected="action to succeed", retry_step=(step, params))
            return _StepFlow.EARLY if self._early else _StepFlow.PROCEED

        # per-step checkpoint
        if step.checkpoint and not self._verify(step.checkpoint):
            self._early = self._escalate_or_fail(
                cap, step, "step checkpoint failed",
                expected=step.checkpoint.description
                or str(step.checkpoint.locator.value),
                retry_step=(step, params))
            return _StepFlow.EARLY if self._early else _StepFlow.PROCEED

        return _StepFlow.PROCEED

    # ---- outcome taxonomy handling --------------------------------------
    def _handle_state(self, cap: Capability, step: Optional[StepSpec]) -> _StepFlow:
        det = self._match_outcome(cap.outcomes)
        if det is None:
            return _StepFlow.PROCEED
        sid = step.id if step else "<entry>"
        self.log.event("outcome_detected", step=sid, outcome=det.name, kind=det.kind)

        if det.kind == OutcomeKind.BUSINESS:
            self._early = ReplayResult(
                status=ReplayStatus.BUSINESS_OUTCOME, run_id=self.log.run_id,
                outcome_name=det.name, message=det.message, outputs=self.outputs)
            self.log.close(status="business_outcome", outcome=det.name)
            return _StepFlow.EARLY

        if det.kind == OutcomeKind.RECOVERABLE:
            if self._recover(det):
                return _StepFlow.PROCEED
            self._early = self._escalate_or_fail(
                cap, step, f"recovery for {det.name} exhausted",
                expected=det.message or det.name)
            return _StepFlow.EARLY if self._early else _StepFlow.PROCEED

        # HARD
        self._early = self._escalate_or_fail(
            cap, step, f"hard outcome: {det.name}",
            expected=det.message or det.name, allow_escalate=det.escalate)
        return _StepFlow.EARLY if self._early else _StepFlow.PROCEED

    def _match_outcome(self, detectors: List[OutcomeDetector]) -> Optional[OutcomeDetector]:
        p = self.surface.perceive()
        hay = f"{p.title}\n{p.text_digest}"
        for d in detectors:
            m: Match = d.when
            if m.status_in and p.status not in m.status_in:
                continue
            if m.text_contains and m.text_contains not in hay:
                continue
            if m.locator_present is not None:
                r = self.surface.act(Action(type=ActionType.WAIT_FOR,
                                            locator=m.locator_present))
                if not r.ok:
                    continue
            # at least one condition must have been asserted
            if not (m.status_in or m.text_contains or m.locator_present):
                continue
            return d
        return None

    def _recover(self, det: OutcomeDetector) -> bool:
        rec = det.recover
        if rec is None:
            return False
        for attempt in range(1, rec.max_attempts + 1):
            self.log.event("recover_attempt", outcome=det.name, kind=rec.kind,
                           attempt=attempt)
            if rec.kind == RecoverKind.DISMISS and rec.locator is not None:
                self.surface.act(Action(type=ActionType.CLICK, locator=rec.locator))
            elif rec.kind == RecoverKind.RETRY:
                # Re-load the current URL. Correct for a transient page-load
                # failure: the control that got us here may no longer exist on
                # the error page, but the URL is still the thing to retry.
                current = self.surface.perceive().url
                self._act(Action(type=ActionType.NAVIGATE, value=current))
            elif rec.kind == RecoverKind.WAIT:
                time.sleep(rec.wait_seconds)
            # re-check: did the condition clear?
            if self._match_outcome([det]) is None:
                self.log.event("recover_ok", outcome=det.name, attempt=attempt)
                return True
            time.sleep(rec.wait_seconds)
        return False

    # ---- escalation ------------------------------------------------------
    def _escalate_or_fail(self, cap, step, reason, expected="", observed=None,
                          retry_step=None, allow_escalate=True) -> ReplayResult:
        p = self.surface.perceive()
        observed = observed if observed is not None else p.digest()[:300]
        snap = self.log.snapshot("replay_stuck", self.surface.snapshot())

        if self.coordinator is None or not allow_escalate:
            return self._fail(step.id if step else None, reason, observed,
                              expected=expected, evidence=snap)

        req = InterventionRequest(
            run_id=self.log.run_id, capability_id=cap.id, goal=cap.goal,
            step_id=step.id if step else "<entry>", reason=reason,
            perception_digest=p.digest(), snapshot_ref=snap)
        decision = self.coordinator.escalate(req, self.surface)

        if decision.kind == DecisionKind.ABORT:
            return self._fail(step.id if step else None,
                              f"operator aborted: {reason}", observed,
                              expected=expected, evidence=snap)

        # RESUME: human changed the live session. Re-attempt the step (once).
        if retry_step is not None:
            rstep, rparams = retry_step
            ex = rstep.action.model_copy(deep=True)
            ex.value = substitute(rstep.action.value, rparams)
            res = self._act(ex)
            self._capture_output(rstep, res)
            self.log.event("resume_reexec", step=rstep.id, ok=res.ok)
            if not res.ok:
                return self._fail(rstep.id, f"re-exec failed after handoff: "
                                  f"{res.error}", self.surface.perceive().digest(),
                                  expected=expected, evidence=snap)
            if rstep.checkpoint and not self._verify(rstep.checkpoint):
                return self._fail(rstep.id, "checkpoint still failing after "
                                  "handoff", self.surface.perceive().digest(),
                                  expected=expected, evidence=snap)
        # resumed cleanly; signal proceed by clearing _early
        self._early = None
        return None  # type: ignore[return-value]

    # ---- helpers ---------------------------------------------------------
    def _act(self, action: Action):
        res = self.surface.act(action)
        if action.type in (ActionType.NAVIGATE, ActionType.CLICK,
                            ActionType.FILL, ActionType.SELECT):
            self._last_action = action
        return res

    def _verify(self, cp: Checkpoint) -> bool:
        return self.surface.act(Action(type=ActionType.WAIT_FOR,
                                       locator=cp.locator)).ok

    def _capture_output(self, step: StepSpec, res) -> None:
        if step.action.output_name and res.extracted is not None:
            self.outputs[step.action.output_name] = res.extracted
            self.log.event("extract", step=step.id, output=step.action.output_name,
                           value=redact_value(step.action.output_name,
                                              res.extracted, sensitive=True))

    def _redact_output(self, cap, name, value):
        spec = next((o for o in cap.outputs if o.name == name), None)
        return redact_value(name, value, sensitive=bool(spec and spec.sensitive))

    def _validate_params(self, specs: List[ParamSpec], params: dict) -> Optional[str]:
        for s in specs:
            if s.required and (s.name not in params or params[s.name] == ""):
                if s.name in ("account_type",):  # allow empty to demo validation
                    continue
                return f"missing required param {s.name!r}"
            if s.pattern and s.name in params:
                if not re.fullmatch(s.pattern, str(params[s.name])):
                    return f"param {s.name!r} fails pattern {s.pattern!r}"
        return None

    def _fail(self, step_id, reason, observed, expected="", evidence=None):
        self.log.event("replay_failure", step=step_id, reason=reason,
                       expected=expected)
        self.log.close(status="failure", step=step_id)
        return ReplayResult(
            status=ReplayStatus.FAILURE, run_id=self.log.run_id,
            failed_step=step_id, message=reason, expected=expected,
            observed=observed, evidence_ref=evidence)
