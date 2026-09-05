"""
HTTP API for the frontend.

Wraps the same discover / replay / artifact pipeline the CLI uses as JSON
endpoints, and exposes each run's redacted event log so the UI can render a
live timeline. No new execution logic lives here -- this is a thin adapter
over agent.loop.AgentLoop, replay.engine.Replayer, artifact.store.ArtifactStore
and escalation.handoff, identical to what cli.py calls.

Run with:  PYTHONPATH=src python -m cua.api
Listens on http://127.0.0.1:5050

The mock bank is started ONCE for the life of this process (unlike the CLI,
which starts/stops it per command), so discover/replay calls reset the
injected transient-failure counters explicitly to keep demo scenarios
repeatable from the UI.
"""
from __future__ import annotations

import glob
import json
import os
import shutil

from flask import Flask, jsonify, request

from .mockbank import server as mock
from .mockbank import app as mockbank_app
from .surface.http_html import HttpHtmlSurface
from .observability.log import RunLogger
from .agent.planner import make_planner
from .agent.loop import AgentLoop
from .replay.engine import Replayer
from .escalation.handoff import HandoffCoordinator, MockOperator
from .artifact.store import ArtifactStore
from .cli import PRESETS, default_policy

EVIDENCE = "evidence"
ARTIFACTS = "artifacts"

DEMO_MEMBERS = [
    {"id": "12345", "name": "Alicia Fenwick", "note": "Standard lookup"},
    {"id": "55555", "name": "Priya Raman", "note": "Privacy interstitial (auto-resolved)"},
    {"id": "60503", "name": "Nadia Toft", "note": "Transient outage (auto-retried)"},
    {"id": "77777", "name": "Dev Okafor", "note": "Needs a supervisor override"},
    {"id": "00000", "name": "\u2014", "note": "No such member"},
    {"id": "99999", "name": "\u2014", "note": "Not authorized"},
]

app = Flask(__name__)


@app.after_request
def add_cors(resp):
    # Belt-and-suspenders: the Vite dev server proxies /api, but this keeps
    # the API usable directly (e.g. `vite preview`, or a different origin).
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp


def _params(body: dict) -> dict:
    p = {"operator_user": "svc_agent", "operator_pass": "correct horse battery"}
    if body.get("member_id") is not None:
        p["member_id"] = body["member_id"]
    if body.get("account_type") is not None:
        p["account_type"] = body["account_type"]
    return p


def _events(run_id: str):
    path = os.path.join(EVIDENCE, run_id, "run.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


@app.route("/api/presets")
def presets():
    return jsonify({
        "presets": {k: {kk: vv for kk, vv in v.items()} for k, v in PRESETS.items()},
        "demo_members": DEMO_MEMBERS,
    })


@app.route("/api/artifacts")
def list_artifacts():
    store = ArtifactStore(ARTIFACTS)
    out = []
    for name in store.list():
        cap = store.load_path(os.path.join(ARTIFACTS, name))
        out.append({
            "id": cap.id, "version": cap.version, "name": cap.name,
            "approval": cap.approval, "steps": len(cap.steps),
            "params": [p.name for p in cap.params],
            "outputs": [o.name for o in cap.outputs],
            "filename": name,
        })
    return jsonify(out)


@app.route("/api/artifacts/<cap_id>")
def show_artifact(cap_id):
    store = ArtifactStore(ARTIFACTS)
    try:
        cap = store.load(cap_id)
    except FileNotFoundError:
        return jsonify({"error": "not found"}), 404
    return jsonify(json.loads(cap.to_json()))


@app.route("/api/artifacts/<cap_id>/approve", methods=["POST"])
def approve_artifact(cap_id):
    store = ArtifactStore(ARTIFACTS)
    try:
        cap = store.load(cap_id)
    except FileNotFoundError:
        return jsonify({"error": "not found"}), 404
    cap.approval = "approved"
    store.save(cap)
    return jsonify({"ok": True, "id": cap.id, "approval": cap.approval})


@app.route("/api/discover", methods=["POST"])
def discover():
    body = request.get_json(force=True) or {}
    preset_name = body.get("preset", "balance")
    if preset_name not in PRESETS:
        return jsonify({"error": f"unknown preset {preset_name!r}"}), 400
    preset = PRESETS[preset_name]
    mockbank_app.reset_transient_state()

    surface = HttpHtmlSurface(mock.BASE_URL)
    log = RunLogger(EVIDENCE, kind="discovery")
    loop = AgentLoop(surface, make_planner("mock"), default_policy(), log)
    res = loop.discover(app_id="coreserv-7.2", entry="/search",
                        surface_kind="http_html", params=_params(body), **preset)
    if not res.ok:
        log.close(status="discovery_failed", reason=res.reason)
        return jsonify({"ok": False, "reason": res.reason, "run_id": res.run_id,
                        "events": _events(res.run_id)})

    store = ArtifactStore(ARTIFACTS)
    path = store.save(res.capability)
    log.close(status="discovery_success", artifact=path)
    return jsonify({
        "ok": True, "run_id": res.run_id,
        "capability": json.loads(res.capability.to_json()),
        "events": _events(res.run_id),
    })


@app.route("/api/replay", methods=["POST"])
def replay():
    body = request.get_json(force=True) or {}
    preset_name = body.get("preset", "balance")
    if preset_name not in PRESETS:
        return jsonify({"error": f"unknown preset {preset_name!r}"}), 400
    cap_id = PRESETS[preset_name]["cap_id"]

    store = ArtifactStore(ARTIFACTS)
    try:
        cap = store.load(cap_id)
    except FileNotFoundError:
        return jsonify({"error": f"no artifact for {cap_id!r} yet -- "
                        f"run discovery first"}), 400

    mockbank_app.reset_transient_state()
    if body.get("simulate"):
        cap.target.entry = f"{cap.target.entry}?simulate={body['simulate']}"

    surface = HttpHtmlSurface(mock.BASE_URL)
    log = RunLogger(EVIDENCE, kind="replay")
    coordinator = HandoffCoordinator(MockOperator(), log) if body.get("operator") else None
    r = Replayer(surface, default_policy(), log, coordinator=coordinator,
                confirm_risky=bool(body.get("confirm_risky")),
                allow_draft=bool(body.get("allow_draft")))
    result = r.replay(cap, _params(body))
    return jsonify({
        "status": result.status.value, "run_id": result.run_id,
        "outputs": result.outputs, "outcome_name": result.outcome_name,
        "message": result.message, "failed_step": result.failed_step,
        "expected": result.expected, "observed": result.observed,
        "evidence_ref": result.evidence_ref,
        "events": _events(result.run_id),
    })


@app.route("/api/runs")
def list_runs():
    runs = []
    for d in glob.glob(os.path.join(EVIDENCE, "*/")):
        run_id = os.path.basename(d.rstrip("/"))
        evs = _events(run_id)
        if not evs:
            continue
        fin = next((e for e in reversed(evs) if e["event"] == "run_finished"), {})
        runs.append({
            "run_id": run_id, "kind": evs[0].get("kind"),
            "status": fin.get("status", "unknown"),
            "started": evs[0]["ts"],
        })
    runs.sort(key=lambda r: r["started"], reverse=True)
    return jsonify(runs[:50])


@app.route("/api/runs/<run_id>")
def show_run(run_id):
    evs = _events(run_id)
    if not evs:
        return jsonify({"error": "not found"}), 404
    return jsonify({"run_id": run_id, "events": evs})


@app.route("/api/reset", methods=["POST"])
def reset_all():
    """Wipe artifacts + evidence and reset mock-app counters, for a clean demo."""
    for d in (ARTIFACTS, EVIDENCE):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
    mockbank_app.reset_transient_state()
    return jsonify({"ok": True})


def main():
    srv = mock.start()
    print(f"mock bank running at {mock.BASE_URL}")
    print("API listening at http://127.0.0.1:5050")
    try:
        app.run(host="127.0.0.1", port=5050, debug=False)
    finally:
        srv.stop()


if __name__ == "__main__":
    main()
