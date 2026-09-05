"""
Discovery loop: goal + live surface -> successful run -> Capability artifact.

The LLM (planner) is in the decision loop here and ONLY here. Every action is
policy-checked before execution; risky steps are recorded but, in discovery, we
allow them (we are exploring with a supervised, non-production session) while
flagging them for the artifact. Concrete param values are used to drive the
surface but the recorded steps stay templated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..surface.base import Surface, Action, ActionType
from ..safety.policy import Policy, redact_value
from ..observability.log import RunLogger
from ..artifact.schema import Capability, StepSpec, Checkpoint
from ..artifact.record import build_capability
from .planner import Planner, PlanStatus
from .templating import substitute


@dataclass
class DiscoveryResult:
    ok: bool
    capability: Optional[Capability] = None
    reason: Optional[str] = None
    run_id: str = ""


class AgentLoop:
    def __init__(self, surface: Surface, planner: Planner, policy: Policy,
                 logger: RunLogger, max_steps: int = 25):
        self.surface = surface
        self.planner = planner
        self.policy = policy
        self.log = logger
        self.max_steps = max_steps

    def discover(self, *, goal: str, cap_id: str, name: str, description: str,
                 app_id: str, entry: str, surface_kind: str,
                 params: dict) -> DiscoveryResult:
        self.log.event("discovery_goal", goal=goal, cap_id=cap_id,
                       params=sorted(params.keys()))

        ok, why = self.policy.allows_navigation(entry)
        if not ok:
            self.log.event("policy_block", where="entry", reason=why)
            return DiscoveryResult(ok=False, reason=f"blocked: {why}",
                                   run_id=self.log.run_id)
        self.surface.act(Action(type=ActionType.NAVIGATE, value=entry))

        history: list[StepSpec] = []
        last_checkpoint: Optional[Checkpoint] = None

        for i in range(self.max_steps):
            perception = self.surface.perceive()
            self.log.event("perceive", step=i, state=perception.digest())

            plan = self.planner.next_step(goal, params, perception, history)
            if plan.status == PlanStatus.DONE:
                self.log.event("goal_reached", reason=plan.reason)
                break
            if plan.status == PlanStatus.STUCK:
                self.log.event("stuck", reason=plan.reason)
                self.log.snapshot("stuck", self.surface.snapshot())
                return DiscoveryResult(ok=False, reason=plan.reason,
                                       run_id=self.log.run_id)

            step = plan.step
            allowed, why = self.policy.allows_action(step.action)
            if not allowed:
                self.log.event("policy_block", step=step.id, reason=why)
                return DiscoveryResult(ok=False, reason=f"blocked: {why}",
                                       run_id=self.log.run_id)
            if step.risky:
                self.log.event("risky_action", step=step.id,
                               note="permitted in supervised discovery; flagged")

            # Execute with concrete values; record templated form.
            exec_action = step.action.model_copy(deep=True)
            exec_action.value = substitute(step.action.value, params)
            res = self.surface.act(exec_action)

            logged_val = None
            if step.action.value is not None:
                # log the templated form, never the concrete PII value
                logged_val = step.action.value
            self.log.event("act", step=step.id, action=step.action.type,
                           value=logged_val, ok=res.ok,
                           resolved_by=res.resolved_by, error=res.error)

            if not res.ok:
                self.log.snapshot("act_failed", self.surface.snapshot())
                return DiscoveryResult(ok=False,
                                       reason=f"step {step.id} failed: {res.error}",
                                       run_id=self.log.run_id)

            if res.extracted is not None and step.action.output_name:
                self.log.event("extract", step=step.id,
                               output=step.action.output_name,
                               value=redact_value(step.action.output_name,
                                                  res.extracted, sensitive=True))

            if step.checkpoint:
                cp_ok = self._verify(step.checkpoint)
                self.log.event("checkpoint", step=step.id, ok=cp_ok)
                if not cp_ok:
                    return DiscoveryResult(
                        ok=False, reason=f"checkpoint failed at {step.id}",
                        run_id=self.log.run_id)
                last_checkpoint = step.checkpoint

            history.append(step)
        else:
            return DiscoveryResult(ok=False, reason="max steps reached",
                                   run_id=self.log.run_id)

        if last_checkpoint is None:
            return DiscoveryResult(ok=False, reason="no checkpoint captured",
                                   run_id=self.log.run_id)

        cap = build_capability(
            cap_id=cap_id, name=name, description=description, goal=goal,
            app_id=app_id, entry=entry, surface_kind=surface_kind,
            steps=history, run_id=self.log.run_id, planner=self.planner.name,
            success=last_checkpoint)
        self.log.event("artifact_built", cap_id=cap.id, version=cap.version,
                       steps=len(cap.steps), params=[p.name for p in cap.params],
                       outputs=[o.name for o in cap.outputs])
        return DiscoveryResult(ok=True, capability=cap, run_id=self.log.run_id)

    def _verify(self, cp: Checkpoint) -> bool:
        res = self.surface.act(Action(type=ActionType.WAIT_FOR, locator=cp.locator))
        return res.ok
