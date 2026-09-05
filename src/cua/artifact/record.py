"""
Recorder: discovery steps -> a reviewable Capability artifact.

Params are *inferred* from the {{templates}} the planner used, so the concrete
values that actually drove discovery (which may be PII) are never persisted.
Outputs are inferred from EXTRACT steps. A default, app-scoped error taxonomy
is attached; in a real system these outcomes would be proposed during discovery
and curated at review time.
"""
from __future__ import annotations

from .schema import (
    Capability, StepSpec, ParamSpec, ParamType, OutputSpec, Checkpoint,
    TargetSpec, Provenance, OutcomeDetector, OutcomeKind, Match, RecoverAction,
    RecoverKind,
)
from ..surface.base import Locator, Strategy
from ..agent.templating import find_params

_SENSITIVE_HINTS = ("pass", "secret", "token", "pin", "ssn")


def _infer_params(steps: list[StepSpec]) -> list[ParamSpec]:
    seen: dict[str, ParamSpec] = {}
    for s in steps:
        for name in find_params(s.action.value):
            if name in seen:
                continue
            sensitive = any(h in name.lower() for h in _SENSITIVE_HINTS)
            seen[name] = ParamSpec(
                name=name, type=ParamType.STRING, required=True,
                sensitive=sensitive,
                description=f"input used at step {s.id!r}")
    return list(seen.values())


def _infer_outputs(steps: list[StepSpec]) -> list[OutputSpec]:
    outs = []
    for s in steps:
        if s.action.output_name:
            # financial figures are sensitive: returned to caller but redacted in logs
            sens = any(k in s.action.output_name for k in ("balance", "amount"))
            outs.append(OutputSpec(
                name=s.action.output_name, type=ParamType.STRING, sensitive=sens,
                description=f"extracted at step {s.id!r}"))
    return outs


def default_outcomes(app_id: str) -> list[OutcomeDetector]:
    """Declarative error taxonomy for the CoreServ mock app."""
    return [
        OutcomeDetector(
            name="member_not_found", kind=OutcomeKind.BUSINESS,
            when=Match(status_in=[404], text_contains="No member found"),
            message="No member exists for the supplied id."),
        OutcomeDetector(
            name="permission_denied", kind=OutcomeKind.BUSINESS,
            when=Match(status_in=[403], text_contains="not authorized"),
            message="Operator is not authorized to view this member."),
        OutcomeDetector(
            name="validation_error", kind=OutcomeKind.BUSINESS,
            when=Match(text_contains="is required"),
            message="A required field was rejected by the app."),
        OutcomeDetector(
            name="privacy_interstitial", kind=OutcomeKind.RECOVERABLE,
            when=Match(text_contains="Privacy notice"),
            recover=RecoverAction(
                kind=RecoverKind.DISMISS,
                locator=Locator(strategy=Strategy.LINK, value="Acknowledge"),
                max_attempts=1),
            message="Dismissed the privacy acknowledgement interstitial."),
        OutcomeDetector(
            name="transient_unavailable", kind=OutcomeKind.RECOVERABLE,
            when=Match(status_in=[503], text_contains="temporarily unavailable"),
            recover=RecoverAction(kind=RecoverKind.RETRY, max_attempts=3,
                                  wait_seconds=0.3),
            message="Service returned a transient error; retried."),
        OutcomeDetector(
            name="app_error", kind=OutcomeKind.HARD,
            when=Match(status_in=[500]),
            escalate=True,
            message="The application returned an internal error."),
    ]


def build_capability(*, cap_id: str, name: str, description: str, goal: str,
                     app_id: str, entry: str, surface_kind: str,
                     steps: list[StepSpec], run_id: str, planner: str,
                     success: Checkpoint) -> Capability:
    return Capability(
        id=cap_id, name=name, description=description, goal=goal,
        target=TargetSpec(app_id=app_id, entry=entry, surface_kind=surface_kind),
        params=_infer_params(steps),
        outputs=_infer_outputs(steps),
        steps=steps,
        success=success,
        outcomes=default_outcomes(app_id),
        provenance=Provenance(discovery_run_id=run_id, planner=planner),
        approval="draft",
    )
