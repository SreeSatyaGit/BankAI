import Lamp from "./Lamp.jsx";

function lampFor(status) {
  if (["success", "discovery_success"].includes(status)) return "success";
  if (status === "business_outcome") return "info";
  if (["failure", "discovery_failed"].includes(status)) return "danger";
  return "idle";
}

function relTime(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 5) return "just now";
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

export default function HistoryPanel({ runs, onSelect }) {
  return (
    <div className="panel">
      <p className="panel-title">Run history</p>
      {runs.length === 0 && <p className="empty-note">No runs yet.</p>}
      <div className="history-list">
        {runs.map((r) => (
          <button className="history-row" key={r.run_id} onClick={() => onSelect(r.run_id)}>
            <Lamp variant={lampFor(r.status)} />
            <span className="history-id">{r.run_id}</span>
            <span className="history-meta">{relTime(r.started)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
