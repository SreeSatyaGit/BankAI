"""
cua -- command line for the discover / replay / escalate pipeline.

Examples (see README for the full demo path):
  python -m cua.cli discover --preset balance    --member-id 12345
  python -m cua.cli approve  --cap read_savings_balance
  python -m cua.cli replay   --preset balance    --member-id 12345
  python -m cua.cli replay   --preset balance    --member-id 00000   # not found
  python -m cua.cli replay   --preset balance    --member-id 55555   # interstitial
  python -m cua.cli replay   --preset balance    --member-id 77777 --operator  # escalation
  python -m cua.cli replay   --preset balance    --member-id 12345 --simulate timeout
"""
from __future__ import annotations

import argparse
import json
import sys

from .mockbank import server as mock
from .surface.http_html import HttpHtmlSurface
from .surface.base import ActionType
from .safety.policy import Policy
from .observability.log import RunLogger
from .agent.planner import make_planner
from .agent.loop import AgentLoop
from .replay.engine import Replayer, ReplayStatus
from .escalation.handoff import HandoffCoordinator, MockOperator
from .artifact.store import ArtifactStore

EVIDENCE = "evidence"
ARTIFACTS = "artifacts"

PRESETS = {
    "balance": dict(
        cap_id="read_savings_balance",
        name="Read member savings balance",
        description="Look up a member and return their current savings balance.",
        goal="look up a member and read their current savings balance",
    ),
    "subaccount": dict(
        cap_id="open_subaccount",
        name="Open a member sub-account",
        description="Open a new sub-account for a member and reach the "
                    "confirmation screen.",
        goal="open a new sub-account for this member and reach the confirmation "
             "screen",
    ),
}


def default_policy() -> Policy:
    return Policy(
        allowed_hosts=[f"{mock.HOST}:{mock.PORT}"],
        allowed_path_prefixes=["/"],
        allowed_actions=list(ActionType),
        require_confirmation_for_risky=True,
    )


def _params(args) -> dict:
    p = {"operator_user": "svc_agent", "operator_pass": "correct horse battery"}
    if args.member_id is not None:
        p["member_id"] = args.member_id
    if getattr(args, "account_type", None) is not None:
        p["account_type"] = args.account_type
    return p


def cmd_discover(args):
    srv = mock.start()
    try:
        preset = PRESETS[args.preset]
        surface = HttpHtmlSurface(mock.BASE_URL)
        log = RunLogger(EVIDENCE, kind="discovery")
        loop = AgentLoop(surface, make_planner(args.planner), default_policy(), log)
        res = loop.discover(
            app_id="coreserv-7.2", entry="/search", surface_kind="http_html",
            params=_params(args), **preset)
        if not res.ok:
            log.close(status="discovery_failed", reason=res.reason)
            print(f"discovery FAILED: {res.reason}", file=sys.stderr)
            return 1
        store = ArtifactStore(ARTIFACTS)
        path = store.save(res.capability)
        log.close(status="discovery_success", artifact=path)
        print(f"discovery OK  run={res.run_id}")
        print(f"artifact saved: {path}")
        print(f"  params : {[p.name for p in res.capability.params]}")
        print(f"  outputs: {[o.name for o in res.capability.outputs]}")
        return 0
    finally:
        srv.stop()


def cmd_replay(args):
    srv = mock.start()
    try:
        store = ArtifactStore(ARTIFACTS)
        cap = store.load(PRESETS[args.preset]["cap_id"])
        if args.simulate:
            cap.target.entry = f"{cap.target.entry}?simulate={args.simulate}"

        surface = HttpHtmlSurface(mock.BASE_URL)
        log = RunLogger(EVIDENCE, kind="replay")
        coordinator = None
        if args.operator:
            coordinator = HandoffCoordinator(MockOperator(), log)

        r = Replayer(surface, default_policy(), log, coordinator=coordinator,
                     confirm_risky=args.confirm_risky, allow_draft=args.allow_draft)
        result = r.replay(cap, _params(args))
        _print_result(result)
        return 0 if result.status != ReplayStatus.FAILURE else 2
    finally:
        srv.stop()


def _print_result(result):
    print(f"replay {result.status.value.upper()}  run={result.run_id}")
    if result.status == ReplayStatus.SUCCESS:
        print(f"  outputs: {json.dumps(result.outputs)}")
    elif result.status == ReplayStatus.BUSINESS_OUTCOME:
        print(f"  outcome: {result.outcome_name} -- {result.message}")
    else:
        print(f"  failed at step: {result.failed_step}")
        print(f"  reason  : {result.message}")
        print(f"  expected: {result.expected}")
        print(f"  observed: {result.observed}")
        print(f"  evidence: {result.evidence_ref}")


def cmd_list(args):
    for name in ArtifactStore(ARTIFACTS).list():
        print(name)
    return 0


def cmd_show(args):
    cap = ArtifactStore(ARTIFACTS).load(args.cap)
    print(cap.to_json())
    return 0


def cmd_approve(args):
    store = ArtifactStore(ARTIFACTS)
    cap = store.load(args.cap)
    cap.approval = "approved"
    print(f"approved {cap.id}@{cap.version} -> {store.save(cap)}")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="cua")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--preset", choices=list(PRESETS), default="balance")
        p.add_argument("--member-id", default=None)
        p.add_argument("--account-type", default=None)

    d = sub.add_parser("discover", help="LLM-driven discovery run -> artifact")
    add_common(d)
    d.add_argument("--planner", choices=["mock", "claude"], default="mock")
    d.set_defaults(func=cmd_discover)

    r = sub.add_parser("replay", help="deterministic replay of an artifact")
    add_common(r)
    r.add_argument("--operator", action="store_true",
                   help="enable human-in-the-loop escalation (mock operator)")
    r.add_argument("--confirm-risky", action="store_true",
                   help="permit risky/irreversible steps in unattended replay")
    r.add_argument("--allow-draft", action="store_true",
                   help="replay a draft (non-approved) capability")
    r.add_argument("--simulate", default=None,
                   help="inject a runtime condition at entry, e.g. 'timeout'")
    r.set_defaults(func=cmd_replay)

    for nm, fn in (("list", cmd_list), ("show", cmd_show), ("approve", cmd_approve)):
        s = sub.add_parser(nm)
        if nm != "list":
            s.add_argument("--cap", required=True)
        s.set_defaults(func=fn)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
