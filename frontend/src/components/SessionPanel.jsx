import { CheckCircle2, Info, XCircle, Loader2, Users } from "lucide-react";
import Lamp from "./Lamp.jsx";

const ICONS = {
  success: CheckCircle2,
  business_outcome: Info,
  failure: XCircle,
  running: Loader2,
};

const TITLES = {
  success: "Success",
  business_outcome: "Business outcome",
  failure: "Failure",
  running: "Running",
};

function bannerKey(status) {
  if (status === "discovery_success") return "success";
  if (status === "discovery_failed") return "failure";
  return status;
}

function lampForEvent(e) {
  const name = e.event;
  if (["replay_failure", "policy_block", "stuck", "discovery_failed", "risky_blocked"].includes(name)) {
    return "danger";
  }
  if (name === "act" || name === "checkpoint") {
    if (e.ok === false) return "danger";
    if (e.ok === true) return "success";
    return "idle";
  }
  if (["outcome_detected", "risky_action", "recover_attempt", "escalation_raised",
       "control_transfer", "human_action"].includes(name)) {
    return "attn";
  }
  if (["recover_ok", "replay_success", "goal_reached", "discovery_success",
       "artifact_built"].includes(name)) {
    return "success";
  }
  if (name === "escalation_resolved") {
    return e.decision === "resume" ? "success" : "danger";
  }
  return "idle";
}

function detailFor(e) {
  const skip = new Set(["ts", "run_id", "event"]);
  return Object.entries(e)
    .filter(([k]) => !skip.has(k))
    .map(([k, v]) => {
      if (v === null || v === undefined || v === "") return null;
      if (typeof v === "object") return `${k}=${JSON.stringify(v)}`;
      return `${k}=${v}`;
    })
    .filter(Boolean)
    .join("   ");
}

function ts(iso) {
  try {
    return iso.split("T")[1].slice(0, 8);
  } catch {
    return iso;
  }
}

export default function SessionPanel({ result }) {
  if (!result) {
    return (
      <div className="session-scroll">
        <div className="session-empty">
          No session yet. Pick a case on the left, then run discovery or replay.
        </div>
      </div>
    );
  }

  const key = bannerKey(result.status);
  const Icon = ICONS[key] || Info;
  const hasHandoff = (result.events || []).some((e) => e.event === "control_transfer");

  return (
    <div className="session-scroll">
      <div
        key={result.run_id}
        className={`result-banner status-${key}${hasHandoff ? " handoff-pulse" : ""}`}
      >
        <span className="result-icon">
          <Icon size={17} className={key === "running" ? "spin-icon" : ""}
                style={key === "running" ? { animation: "spin 0.9s linear infinite" } : undefined} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="result-title">
            {TITLES[key] || key}
            {result.outcome_name ? ` — ${result.outcome_name}` : ""}
          </div>
          <div className="result-detail">
            {result.message || result.reason || "—"}
            {result.failed_step && <> at step <code>{result.failed_step}</code></>}
          </div>
        </div>
        {hasHandoff && (
          <span className="pill" style={{ borderColor: "var(--amber)", color: "var(--amber)" }}>
            <Users size={11} /> handed to operator
          </span>
        )}
      </div>

      {result.expected && (
        <div className="kv-list" style={{ marginBottom: 18 }}>
          <div className="kv-row">
            <span className="kv-key">Expected</span>
            <span className="kv-val">{result.expected}</span>
          </div>
          {result.observed && (
            <div className="kv-row">
              <span className="kv-key">Observed</span>
              <span className="kv-val">{result.observed.split("\n")[0]}</span>
            </div>
          )}
        </div>
      )}

      {result.outputs && Object.keys(result.outputs).length > 0 && (
        <div className="outputs-grid">
          {Object.entries(result.outputs).map(([k, v]) => (
            <div className="output-cell" key={k}>
              <div className="output-key">{k}</div>
              <div className="output-val">{v}</div>
            </div>
          ))}
        </div>
      )}

      <div className="timeline-title-row">
        <p className="panel-title" style={{ margin: 0 }}>
          Event stream — {result.run_id}
        </p>
      </div>
      <div className="timeline">
        {(result.events || []).map((e, i) => (
          <div className="tl-row" key={i}>
            <span className="tl-ts">{ts(e.ts)}</span>
            <Lamp variant={lampForEvent(e)} className="tl-lamp" />
            <span className={`tl-event ev-${e.event}`}>{e.event}</span>
            <span className="tl-detail">{detailFor(e)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
