import { useState } from "react";

export default function ArtifactPanel({ artifact, onApprove, busy }) {
  const [showJson, setShowJson] = useState(false);

  if (!artifact) {
    return (
      <div className="panel">
        <p className="panel-title">Artifact</p>
        <p className="empty-note">Nothing discovered yet for this scenario.</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <p className="panel-title">Artifact</p>

      <div className="kv-list">
        <div className="kv-row">
          <span className="kv-key">id</span>
          <span className="kv-val">{artifact.id}</span>
        </div>
        <div className="kv-row">
          <span className="kv-key">version</span>
          <span className="kv-val">{artifact.version}</span>
        </div>
        <div className="kv-row">
          <span className="kv-key">steps</span>
          <span className="kv-val">{artifact.steps?.length ?? artifact.steps}</span>
        </div>
        <div className="kv-row">
          <span className="kv-key">approval</span>
          <span className={"pill " + (artifact.approval === "approved" ? "pill-approved" : "pill-draft")}>
            {artifact.approval}
          </span>
        </div>
      </div>

      <label className="field-label">Params</label>
      <div className="chip-list">
        {(artifact.params || []).map((p) => {
          const name = typeof p === "string" ? p : p.name;
          const sensitive = typeof p === "object" && p.sensitive;
          return (
            <span className={"chip" + (sensitive ? " sensitive" : "")} key={name}>
              {name}
            </span>
          );
        })}
      </div>

      <label className="field-label">Outputs</label>
      <div className="chip-list">
        {(artifact.outputs || []).map((o) => {
          const name = typeof o === "string" ? o : o.name;
          const sensitive = typeof o === "object" && o.sensitive;
          return (
            <span className={"chip" + (sensitive ? " sensitive" : "")} key={name}>
              {name}
            </span>
          );
        })}
      </div>

      {Array.isArray(artifact.steps) && (
        <>
          <label className="field-label">Steps</label>
          <div className="steps-list">
            {artifact.steps.map((s, i) => (
              <div className="step-row" key={s.id}>
                <span className="step-idx">{i + 1}</span>
                <div className="step-desc">
                  <div>{s.description}</div>
                  <div className="step-action">
                    {s.action?.type}
                    {s.risky && <span className="step-risky"> · risky</span>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="btn-row">
        {artifact.approval !== "approved" && (
          <button className="btn btn-primary" onClick={onApprove} disabled={busy}>
            Approve for replay
          </button>
        )}
        <button className="json-toggle" onClick={() => setShowJson((s) => !s)} style={{ marginTop: 4 }}>
          {showJson ? "Hide" : "View"} raw artifact JSON
        </button>
      </div>

      {showJson && <pre className="json-view">{JSON.stringify(artifact, null, 2)}</pre>}
    </div>
  );
}
