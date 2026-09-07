import React, { useState } from "react";
import { discover } from "./api.js";

export default function App() {
  const [targetUrl, setTargetUrl] = useState("");
  const [goal, setGoal] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function runAgent() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = await discover(targetUrl, goal);
      setResult(res);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div style={styles.page}>
      <h1 style={styles.h1}>BankAI</h1>
      <p style={styles.sub}>
        Paste a live target URL and a goal. The agent drives the real page — nothing here is canned.
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

      {result && (
        <div style={styles.panel}>
          <h2 style={styles.h2}>
            Result: {result.ok ? "✅ success" : "❌ not completed"}
          </h2>
          {result.reason && <p style={styles.reason}>{result.reason}</p>}

          <h3 style={styles.h3}>Steps</h3>
          <ol style={styles.stepList}>
            {result.steps.map((ev, i) => (
              <li key={i} style={styles.stepItem}>
                <pre style={styles.pre}>{JSON.stringify(ev, null, 2)}</pre>
              </li>
            ))}
          </ol>

          <h3 style={styles.h3}>Artifact</h3>
          <pre style={styles.pre}>
            {result.artifact ? JSON.stringify(result.artifact, null, 2) : "(none — run did not succeed)"}
          </pre>
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
  pre: {
    background: "#f6f6f6",
    padding: 12,
    borderRadius: 4,
    overflowX: "auto",
    fontSize: 12,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
};
