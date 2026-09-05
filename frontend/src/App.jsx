import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";
import ScenarioPanel from "./components/ScenarioPanel.jsx";
import SessionPanel from "./components/SessionPanel.jsx";
import ArtifactPanel from "./components/ArtifactPanel.jsx";
import HistoryPanel from "./components/HistoryPanel.jsx";

const CAP_ID = { balance: "read_savings_balance", subaccount: "open_subaccount" };

function fromDiscover(data) {
  return {
    run_id: data.run_id,
    status: data.ok ? "discovery_success" : "discovery_failed",
    message: data.ok
      ? `Captured ${data.capability.steps.length} steps as ${data.capability.id}@${data.capability.version}.`
      : data.reason,
    events: data.events,
  };
}

function fromReplay(data) {
  return {
    run_id: data.run_id,
    status: data.status,
    outcome_name: data.outcome_name,
    message: data.message,
    failed_step: data.failed_step,
    expected: data.expected,
    observed: data.observed,
    outputs: data.outputs,
    events: data.events,
  };
}

function fromHistory(run_id, events) {
  const fin = [...events].reverse().find((e) => e.event === "run_finished") || {};
  const succ = events.find((e) => e.event === "replay_success");
  const failEvt = [...events].reverse().find((e) => e.event === "replay_failure");
  return {
    run_id,
    status: fin.status || "unknown",
    message:
      fin.reason ||
      failEvt?.reason ||
      (fin.status === "discovery_success" ? `Artifact saved to ${fin.artifact}` : undefined),
    failed_step: fin.step || failEvt?.step,
    expected: failEvt?.expected,
    outputs: succ?.outputs || {},
    events,
  };
}

export default function App() {
  const [connected, setConnected] = useState(null);
  const [presetsData, setPresetsData] = useState(null);
  const [preset, setPreset] = useState("balance");
  const [memberId, setMemberId] = useState("12345");
  const [accountType, setAccountType] = useState("Savings");
  const [options, setOptions] = useState({ operator: false, confirmRisky: false, allowDraft: false });

  const [artifacts, setArtifacts] = useState([]);
  const [runs, setRuns] = useState([]);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const refreshArtifacts = useCallback(() => {
    api.artifacts().then(setArtifacts).catch(() => {});
  }, []);
  const refreshRuns = useCallback(() => {
    api.runs().then(setRuns).catch(() => {});
  }, []);

  useEffect(() => {
    api.health().then(() => setConnected(true)).catch(() => setConnected(false));
    api.presets().then(setPresetsData).catch(() => {});
    refreshArtifacts();
    refreshRuns();
  }, [refreshArtifacts, refreshRuns]);

  const currentArtifact = artifacts.find((a) => a.id === CAP_ID[preset]);

  async function handleDiscover() {
    setBusy("discover");
    setError(null);
    try {
      const data = await api.discover({ preset, member_id: memberId, account_type: accountType });
      setResult(fromDiscover(data));
      refreshArtifacts();
      refreshRuns();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleReplay() {
    setBusy("replay");
    setError(null);
    try {
      const data = await api.replay({
        preset,
        member_id: memberId,
        account_type: accountType,
        operator: options.operator,
        confirm_risky: options.confirmRisky,
        allow_draft: options.allowDraft,
      });
      setResult(fromReplay(data));
      refreshRuns();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleApprove() {
    if (!currentArtifact) return;
    setBusy("approve");
    try {
      await api.approve(currentArtifact.id);
      refreshArtifacts();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleReset() {
    if (!window.confirm("Clear all artifacts and run history? This can't be undone.")) return;
    setBusy("reset");
    try {
      await api.reset();
      setResult(null);
      refreshArtifacts();
      refreshRuns();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleSelectHistory(runId) {
    try {
      const data = await api.run(runId);
      setResult(fromHistory(runId, data.events));
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" />
          <span className="brand-name">Bridge</span>
          <span className="brand-sub">supervising the CoreServ 7.2 agent</span>
        </div>
        <div className="conn">
          <span className={`lamp ${connected ? "lamp-success on" : "lamp-danger on"}`} />
          {connected === null ? "Connecting…" : connected ? "Agent bridge connected" : "Agent bridge unreachable"}
        </div>
      </header>

      <div className="layout">
        <div className="rail rail-left">
          <ScenarioPanel
            presetsData={presetsData}
            preset={preset}
            setPreset={setPreset}
            memberId={memberId}
            setMemberId={setMemberId}
            accountType={accountType}
            setAccountType={setAccountType}
            options={options}
            setOptions={setOptions}
            onDiscover={handleDiscover}
            onReplay={handleReplay}
            onReset={handleReset}
            canReplay={!!currentArtifact}
            busy={busy}
          />
        </div>

        <div className="center">
          {error && (
            <div style={{ padding: "14px 20px 0" }}>
              <div className="error-banner">{error}</div>
            </div>
          )}
          <SessionPanel result={result} />
        </div>

        <div className="rail rail-right">
          <ArtifactPanel artifact={currentArtifact} onApprove={handleApprove} busy={!!busy} />
          <HistoryPanel runs={runs} onSelect={handleSelectHistory} />
        </div>
      </div>
    </div>
  );
}
