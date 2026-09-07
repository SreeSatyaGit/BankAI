import React, { useEffect, useRef, useState } from "react";
import { startDiscovery, getRunStatus, resumeRun, submitManualAction } from "./api.js";

const POLL_INTERVAL_MS = 1000;

// Catches any render-time crash in AppContent and shows a visible message
// instead of an unstyled blank page. Without this, a single bad response
// shape (e.g. stale state from a Fast Refresh across a structural change)
// unmounts the whole tree silently — the error only ever shows up in the
// browser console, which is easy to miss.
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("BankAI UI crashed:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={styles.page}>
          <h1 style={styles.h1}>BankAI</h1>
          <div style={styles.error}>
            Something went wrong rendering the UI: {String(this.state.error.message || this.state.error)}
          </div>
          <p style={styles.sub}>
            Try a hard refresh (not just HMR). If it keeps happening, check the browser console for
            the full error and paste it back.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  return (
    <ErrorBoundary>
      <AppContent />
    </ErrorBoundary>
  );
}

// Which resume decisions make sense for each escalation trigger. "abort" is
// always offered — everything else depends on whether there's a specific
// pending step (risky / retry_exhausted) or not (stuck). A planner whose API
// call itself fails is NOT an escalation — that ends the run automatically,
// no human prompt, so it never reaches this panel.
function resumeOptionsFor(trigger) {
  if (trigger === "risky") {
    return [
      { decision: "approve", label: "Approve", style: styles.approveButton },
      { decision: "skip", label: "Skip step", style: styles.skipButton },
      { decision: "abort", label: "Abort run", style: styles.denyButton },
    ];
  }
  if (trigger === "retry_exhausted") {
    return [
      { decision: "retry", label: "Retry step", style: styles.approveButton },
      { decision: "skip", label: "Skip step", style: styles.skipButton },
      { decision: "abort", label: "Abort run", style: styles.denyButton },
    ];
  }
  // "stuck" or anything unrecognized: no specific step to act on, just let
  // the agent try again from wherever manual actions left the page, or give up.
  return [
    { decision: "continue", label: "Continue (let agent try again)", style: styles.approveButton },
    { decision: "abort", label: "Abort run", style: styles.denyButton },
  ];
}

const MANUAL_ACTION_TYPES = ["navigate", "click", "type", "select", "extract", "press_enter"];
const LOCATOR_STRATEGIES = ["label", "role", "placeholder", "text", "name"];

function buildManualActionPayload(form) {
  const action = { type: form.type };
  if (form.type !== "navigate") {
    action.locator = { strategy: form.locatorStrategy, value: form.locatorValue };
    if (form.locatorStrategy === "role" && form.locatorRole) {
      action.locator.role = form.locatorRole;
    }
  }
  if (["navigate", "type", "select"].includes(form.type)) {
    action.value = form.value;
  }
  if (form.type === "extract" && form.outputName) {
    action.output_name = form.outputName;
  }
  return action;
}

function ManualActionForm({ runId, onActionTaken }) {
  const [form, setForm] = useState({
    type: "click",
    locatorStrategy: "role",
    locatorValue: "",
    locatorRole: "button",
    value: "",
    outputName: "",
    description: "",
  });
  const [sending, setSending] = useState(false);
  const [lastResult, setLastResult] = useState(null);
  const [formError, setFormError] = useState(null);

  function update(field) {
    return (e) => setForm((f) => ({ ...f, [field]: e.target.value }));
  }

  async function send() {
    setSending(true);
    setFormError(null);
    try {
      const action = buildManualActionPayload(form);
      const result = await submitManualAction(runId, action, form.description);
      setLastResult(result);
      onActionTaken({ ...result, description: form.description || `Manual ${form.type}` });
    } catch (err) {
      setFormError(err.message || String(err));
    } finally {
      setSending(false);
    }
  }

  const needsLocator = form.type !== "navigate";
  const needsValue = ["navigate", "type", "select"].includes(form.type);

  return (
    <div style={styles.manualForm}>
      <h4 style={styles.manualFormTitle}>Take a manual action on this live session</h4>
      <div style={styles.manualFormRow}>
        <label style={styles.manualLabel}>
          Action type
          <select style={styles.select} value={form.type} onChange={update("type")}>
            {MANUAL_ACTION_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </label>
        {needsLocator && (
          <label style={styles.manualLabel}>
            Locator strategy
            <select style={styles.select} value={form.locatorStrategy} onChange={update("locatorStrategy")}>
              {LOCATOR_STRATEGIES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
        )}
      </div>

      {needsLocator && (
        <div style={styles.manualFormRow}>
          <label style={styles.manualLabel}>
            Locator value
            <input style={styles.input} type="text" value={form.locatorValue} onChange={update("locatorValue")} placeholder="e.g. Submit, First name..." />
          </label>
          {form.locatorStrategy === "role" && (
            <label style={styles.manualLabel}>
              Role (for role strategy)
              <input style={styles.input} type="text" value={form.locatorRole} onChange={update("locatorRole")} placeholder="button, link..." />
            </label>
          )}
        </div>
      )}

      {needsValue && (
        <label style={styles.manualLabel}>
          {form.type === "navigate" ? "URL" : "Value to enter"}
          <input style={styles.input} type="text" value={form.value} onChange={update("value")} />
        </label>
      )}

      {form.type === "extract" && (
        <label style={styles.manualLabel}>
          Output name (optional)
          <input style={styles.input} type="text" value={form.outputName} onChange={update("outputName")} placeholder="e.g. balance" />
        </label>
      )}

      <label style={styles.manualLabel}>
        Note (optional, for the log)
        <input style={styles.input} type="text" value={form.description} onChange={update("description")} placeholder="what are you doing and why" />
      </label>

      <button style={{ ...styles.button, ...styles.manualSendButton }} disabled={sending} onClick={send}>
        {sending ? "Sending..." : "Run this action"}
      </button>

      {formError && <div style={styles.error}>Error: {formError}</div>}

      {lastResult && (
        <div style={styles.manualResult}>
          <b>{lastResult.ok ? "✅ succeeded" : "❌ failed"}</b>
          {lastResult.error && <div>{lastResult.error}</div>}
          {lastResult.extracted_text && <div>Extracted: {lastResult.extracted_text}</div>}
          {lastResult.screenshot && (
            <img src={lastResult.screenshot} alt="manual action result" style={styles.screenshot} />
          )}
        </div>
      )}
    </div>
  );
}

function InterventionPanel({ runId, pending, onResumed }) {
  const [resolving, setResolving] = useState(false);
  const [noteInput, setNoteInput] = useState("");
  const [manualLog, setManualLog] = useState([]);
  const [error, setError] = useState(null);

  const trigger = pending.trigger || "unknown";
  const options = resumeOptionsFor(trigger);

  async function respond(decision) {
    setResolving(true);
    setError(null);
    try {
      await resumeRun(runId, decision, noteInput || null);
      onResumed();
      // Next poll tick will pick up the run moving past "awaiting_intervention".
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setResolving(false);
    }
  }

  const triggerLabel = {
    risky: "⚠️ Risky step needs your approval",
    retry_exhausted: "🔁 Step kept failing — needs your input",
    stuck: "🧭 Agent is stuck — take control of the live session",
  }[trigger] || `⏸ Paused (${trigger})`;

  return (
    <div style={styles.interventionBox}>
      <h2 style={styles.interventionTitle}>{triggerLabel}</h2>
      <p style={styles.reason}>{pending.reason}</p>

      {pending.step && (
        <>
          <p><b>Step:</b> {pending.step.description}</p>
          <pre style={styles.pre}>{JSON.stringify(pending.resolved_action ?? {}, null, 2)}</pre>
        </>
      )}

      {pending.perception && (
        <details style={styles.details}>
          <summary>Current page state</summary>
          <p><b>URL:</b> {pending.perception.url}</p>
          <p><b>Title:</b> {pending.perception.title}</p>
          <p style={styles.digest}>{pending.perception.digest}</p>
        </details>
      )}

      {pending.history_tail && pending.history_tail.length > 0 && (
        <details style={styles.details}>
          <summary>Recent history</summary>
          <pre style={styles.pre}>{JSON.stringify(pending.history_tail, null, 2)}</pre>
        </details>
      )}

      {pending.screenshot && (
        <img src={pending.screenshot} alt="current page" style={styles.screenshot} />
      )}

      <ManualActionForm
        runId={runId}
        onActionTaken={(result) => setManualLog((log) => [...log, result])}
      />

      {manualLog.length > 0 && (
        <div style={styles.manualLogBox}>
          <b>Manual actions taken this pause:</b>
          <ul>
            {manualLog.map((m, i) => (
              <li key={i}>{m.ok ? "✅" : "❌"} {m.description}{m.error ? ` — ${m.error}` : ""}</li>
            ))}
          </ul>
        </div>
      )}

      <label style={styles.manualLabel}>
        Note for the run log (optional)
        <input style={styles.input} type="text" value={noteInput} onChange={(e) => setNoteInput(e.target.value)} />
      </label>

      <div style={styles.confirmButtons}>
        {options.map((opt) => (
          <button
            key={opt.decision}
            style={{ ...styles.button, ...opt.style }}
            disabled={resolving}
            onClick={() => respond(opt.decision)}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {error && <div style={styles.error}>Error: {error}</div>}
    </div>
  );
}

function AppContent() {
  const [targetUrl, setTargetUrl] = useState("");
  const [goal, setGoal] = useState("");
  const [runId, setRunId] = useState(null);
  const [status, setStatus] = useState(null); // full status payload from GET /discover/:id
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const pollTimer = useRef(null);

  async function runAgent() {
    setError(null);
    setStatus(null);
    setRunning(true);
    try {
      const res = await startDiscovery(targetUrl, goal);
      setRunId(res.run_id);
    } catch (err) {
      setError(err.message || String(err));
      setRunning(false);
    }
  }

  // Poll for progress whenever a run is active. Stops itself once the run
  // reaches status "done" or the component unmounts.
  useEffect(() => {
    if (!runId) return undefined;
    let cancelled = false;

    async function poll() {
      try {
        const s = await getRunStatus(runId);
        if (cancelled) return;
        setStatus(s);
        if (s.status === "done") {
          setRunning(false);
          return; // stop polling
        }
        pollTimer.current = setTimeout(poll, POLL_INTERVAL_MS);
      } catch (err) {
        if (!cancelled) {
          setError(err.message || String(err));
          setRunning(false);
        }
      }
    }

    poll();
    return () => {
      cancelled = true;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [runId]);

  const pending = status?.status === "awaiting_intervention" ? status.pending_intervention : null;

  return (
    <div style={styles.page}>
      <h1 style={styles.h1}>BankAI</h1>
      <p style={styles.sub}>
        Paste a live target URL and a goal. The agent drives the real page — nothing here is canned.
        Risky steps, a stuck agent, or a step that keeps failing all pause and hand you the live
        session — approve, retry, skip, abort, or act on the page yourself first. If the planner's
        API call itself fails, the run just ends.
      </p>

      <label style={styles.label}>Target URL</label>
      <input
        style={styles.input}
        type="text"
        placeholder="https://example.com"
        value={targetUrl}
        onChange={(e) => setTargetUrl(e.target.value)}
      />

      <label style={styles.label}>Goal</label>
      <textarea
        style={styles.textarea}
        placeholder="e.g. search for 'wireless mouse' and open the first result"
        value={goal}
        onChange={(e) => setGoal(e.target.value)}
      />

      <button
        style={styles.button}
        onClick={runAgent}
        disabled={running || !targetUrl.trim() || !goal.trim()}
      >
        {running ? "Running..." : "Run agent"}
      </button>

      {error && <div style={styles.error}>Error: {error}</div>}

      {pending && (
        <InterventionPanel
          runId={runId}
          pending={pending}
          onResumed={() => {
            /* next poll tick reflects the new state */
          }}
        />
      )}

      {status && (
        <div style={styles.panel}>
          <h2 style={styles.h2}>
            {status.status === "done"
              ? status.ok
                ? "✅ success"
                : "❌ not completed"
              : `⏳ ${status.status}`}
          </h2>
          {status.reason && <p style={styles.reason}>{status.reason}</p>}

          {status.status === "done" && (
            <>
              <h3 style={styles.h3}>Artifact</h3>
              {status.ok && status.artifact ? (
                <div style={styles.artifactCard}>
                  <div style={{ ...styles.artifactBadge, ...styles.artifactBadgeSuccess }}>
                    ✅ Success — capability saved
                  </div>
                  <p style={styles.artifactId}>{status.artifact.id}</p>
                  <p style={styles.reason}>{status.artifact.goal}</p>
                  <button
                    style={{ ...styles.button, ...styles.manualSendButton }}
                    onClick={() => downloadArtifact(status.artifact)}
                  >
                    Download {status.artifact.id}.json
                  </button>
                </div>
              ) : (
                <div style={styles.artifactCard}>
                  <div style={{ ...styles.artifactBadge, ...styles.artifactBadgeFailure }}>
                    ❌ Failed — no artifact was saved
                  </div>
                  {status.reason && <p style={styles.reason}>{status.reason}</p>}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// Builds a real downloadable file client-side from the artifact JSON already
// present in the poll response — no extra backend round trip needed. Named
// after the artifact's own id, matching what's on disk under artifacts/.
function downloadArtifact(artifact) {
  const blob = new Blob([JSON.stringify(artifact, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${artifact.id}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

const styles = {
  page: { maxWidth: 820, margin: "40px auto", padding: "0 20px", fontFamily: "system-ui, sans-serif" },
  h1: { marginBottom: 4 },
  sub: { color: "#555", marginTop: 0 },
  label: { display: "block", marginTop: 16, marginBottom: 4, fontWeight: 600 },
  input: { width: "100%", padding: 8, fontSize: 14, boxSizing: "border-box" },
  select: { width: "100%", padding: 8, fontSize: 14, boxSizing: "border-box" },
  textarea: { width: "100%", padding: 8, fontSize: 14, minHeight: 70, boxSizing: "border-box" },
  button: { marginTop: 16, padding: "8px 16px", fontSize: 14, cursor: "pointer" },
  error: { marginTop: 16, color: "#b00020" },
  panel: { marginTop: 24, borderTop: "1px solid #ddd", paddingTop: 16 },
  h2: { marginBottom: 4 },
  h3: { marginTop: 20, marginBottom: 8 },
  reason: { color: "#555" },
  screenshot: {
    display: "block",
    marginTop: 6,
    maxWidth: "100%",
    border: "1px solid #ddd",
    borderRadius: 4,
  },
  pre: {
    background: "#f6f6f6",
    padding: 12,
    borderRadius: 4,
    overflowX: "auto",
    fontSize: 12,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
  details: { marginTop: 10, fontSize: 13 },
  digest: { color: "#555", fontSize: 12, whiteSpace: "pre-wrap" },

  interventionBox: {
    marginTop: 20,
    padding: 16,
    border: "2px solid #b8860b",
    background: "#fff8e1",
    borderRadius: 6,
  },
  interventionTitle: { marginTop: 0, marginBottom: 8 },
  confirmButtons: { display: "flex", gap: 12, flexWrap: "wrap" },
  approveButton: { background: "#2e7d32", color: "#fff", border: "none" },
  denyButton: { background: "#b00020", color: "#fff", border: "none" },
  skipButton: { background: "#616161", color: "#fff", border: "none" },

  manualForm: {
    marginTop: 16,
    padding: 12,
    background: "#fff",
    border: "1px solid #ddd",
    borderRadius: 6,
  },
  manualFormTitle: { marginTop: 0, marginBottom: 10, fontSize: 14 },
  manualFormRow: { display: "flex", gap: 12, marginBottom: 8, flexWrap: "wrap" },
  manualLabel: {
    display: "block",
    fontSize: 12,
    fontWeight: 600,
    color: "#444",
    marginTop: 8,
    flex: 1,
    minWidth: 160,
  },
  manualSendButton: { background: "#1565c0", color: "#fff", border: "none" },
  manualResult: {
    marginTop: 10,
    padding: 8,
    background: "#f6f6f6",
    borderRadius: 4,
    fontSize: 13,
  },
  manualLogBox: {
    marginTop: 12,
    padding: 8,
    background: "#f0f0f0",
    borderRadius: 4,
    fontSize: 13,
  },
  artifactCard: {
    padding: 16,
    border: "1px solid #ddd",
    borderRadius: 6,
    background: "#fafafa",
  },
  artifactBadge: {
    display: "inline-block",
    padding: "4px 10px",
    borderRadius: 4,
    fontWeight: 600,
    fontSize: 13,
    marginBottom: 8,
  },
  artifactBadgeSuccess: { background: "#e6f4ea", color: "#1e7e34" },
  artifactBadgeFailure: { background: "#fdecea", color: "#b00020" },
  artifactId: { fontFamily: "monospace", fontWeight: 600, marginBottom: 2 },
};
