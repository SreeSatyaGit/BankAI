import React, { useEffect, useRef, useState } from "react";
import { startDiscovery, getRunStatus, confirmRiskyStep } from "./api.js";

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

function AppContent() {
  const [targetUrl, setTargetUrl] = useState("");
  const [goal, setGoal] = useState("");
  const [runId, setRunId] = useState(null);
  const [status, setStatus] = useState(null); // full status payload from GET /discover/:id
  const [running, setRunning] = useState(false);
  const [confirming, setConfirming] = useState(false);
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

  async function respondToRiskyStep(approve) {
    setConfirming(true);
    setError(null);
    try {
      await confirmRiskyStep(runId, approve);
      // Next poll tick will pick up the run moving past "awaiting_confirmation".
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setConfirming(false);
    }
  }

  const steps = status?.steps || [];
  const pending = status?.status === "awaiting_confirmation" ? status.pending_step : null;

  return (
    <div style={styles.page}>
      <h1 style={styles.h1}>BankAI</h1>
      <p style={styles.sub}>
        Paste a live target URL and a goal. The agent drives the real page — nothing here is canned.
        Risky steps (submit, delete, transfer, confirm) pause and wait for your approval.
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
        <div style={styles.confirmBox}>
          <h2 style={styles.confirmTitle}>⚠️ Risky step needs your approval</h2>
          <p>
            <b>{pending.step?.description || "(no description)"}</b>
          </p>
          <pre style={styles.pre}>{JSON.stringify(pending.resolved_action ?? {}, null, 2)}</pre>
          {pending.screenshot && (
            <img src={pending.screenshot} alt="pending step preview" style={styles.screenshot} />
          )}
          <div style={styles.confirmButtons}>
            <button
              style={{ ...styles.button, ...styles.approveButton }}
              disabled={confirming}
              onClick={() => respondToRiskyStep(true)}
            >
              Approve
            </button>
            <button
              style={{ ...styles.button, ...styles.denyButton }}
              disabled={confirming}
              onClick={() => respondToRiskyStep(false)}
            >
              Deny
            </button>
          </div>
        </div>
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

          <h3 style={styles.h3}>Steps</h3>
          <ol style={styles.stepList}>
            {steps.map((ev, i) => (
              <li key={i} style={styles.stepItem}>
                <pre style={styles.pre}>{JSON.stringify(ev, null, 2)}</pre>
                {ev.screenshot && (
                  <img
                    src={ev.screenshot}
                    alt={`screenshot after ${ev.step_id || ev.type}`}
                    style={styles.screenshot}
                  />
                )}
              </li>
            ))}
          </ol>

          {status.status === "done" && (
            <>
              <h3 style={styles.h3}>Artifact</h3>
              <pre style={styles.pre}>
                {status.artifact ? JSON.stringify(status.artifact, null, 2) : "(none — run did not succeed)"}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

const styles = {
  page: { maxWidth: 820, margin: "40px auto", padding: "0 20px", fontFamily: "system-ui, sans-serif" },
  h1: { marginBottom: 4 },
  sub: { color: "#555", marginTop: 0 },
  label: { display: "block", marginTop: 16, marginBottom: 4, fontWeight: 600 },
  input: { width: "100%", padding: 8, fontSize: 14, boxSizing: "border-box" },
  textarea: { width: "100%", padding: 8, fontSize: 14, minHeight: 70, boxSizing: "border-box" },
  button: { marginTop: 16, padding: "8px 16px", fontSize: 14, cursor: "pointer" },
  error: { marginTop: 16, color: "#b00020" },
  panel: { marginTop: 24, borderTop: "1px solid #ddd", paddingTop: 16 },
  h2: { marginBottom: 4 },
  h3: { marginTop: 20, marginBottom: 8 },
  reason: { color: "#555" },
  stepList: { paddingLeft: 20 },
  stepItem: { marginBottom: 8 },
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
  confirmBox: {
    marginTop: 20,
    padding: 16,
    border: "2px solid #b8860b",
    background: "#fff8e1",
    borderRadius: 6,
  },
  confirmTitle: { marginTop: 0, marginBottom: 8 },
  confirmButtons: { display: "flex", gap: 12 },
  approveButton: { background: "#2e7d32", color: "#fff", border: "none" },
  denyButton: { background: "#b00020", color: "#fff", border: "none" },
};
