const PRESET_LABELS = {
  balance: "Balance lookup",
  subaccount: "Open sub-account",
};

export default function ScenarioPanel({
  presetsData,
  preset,
  setPreset,
  memberId,
  setMemberId,
  accountType,
  setAccountType,
  options,
  setOptions,
  onDiscover,
  onReplay,
  onReset,
  canReplay,
  busy,
}) {
  const members = presetsData?.demo_members || [];

  const toggle = (key) => setOptions((o) => ({ ...o, [key]: !o[key] }));

  return (
    <>
      <div className="panel">
        <p className="panel-title">Scenario</p>
        <div className="seg">
          {Object.keys(PRESET_LABELS).map((key) => (
            <button
              key={key}
              className={preset === key ? "active" : ""}
              onClick={() => setPreset(key)}
              disabled={busy}
            >
              {PRESET_LABELS[key]}
            </button>
          ))}
        </div>
      </div>

      <div className="panel">
        <p className="panel-title">Case queue</p>
        <div className="case-list">
          {members.map((m) => (
            <button
              key={m.id}
              className={"case-row" + (memberId === m.id ? " selected" : "")}
              onClick={() => setMemberId(m.id)}
              disabled={busy}
            >
              <span className="case-id">{m.id}</span>
              <span className="case-body">
                <div className="case-name">{m.name}</div>
                <div className="case-note">{m.note}</div>
              </span>
            </button>
          ))}
        </div>

        {preset === "subaccount" && (
          <>
            <label className="field-label" htmlFor="acct-type">
              Account type
            </label>
            <select
              id="acct-type"
              className="text-input"
              value={accountType}
              onChange={(e) => setAccountType(e.target.value)}
              disabled={busy}
            >
              <option value="Savings">Savings</option>
              <option value="Money Market">Money Market</option>
              <option value="">(blank — triggers validation)</option>
            </select>
          </>
        )}
      </div>

      <div className="panel">
        <p className="panel-title">Replay options</p>

        <label className="option-row">
          <input
            type="checkbox"
            checked={options.operator}
            onChange={() => toggle("operator")}
            disabled={busy}
          />
          <span className="option-text">
            <div className="option-title">Operator on standby</div>
            <div className="option-desc">
              If replay gets stuck, hand the live session to a human.
            </div>
          </span>
        </label>

        <label className="option-row">
          <input
            type="checkbox"
            checked={options.confirmRisky}
            onChange={() => toggle("confirmRisky")}
            disabled={busy}
          />
          <span className="option-text">
            <div className="option-title">Confirm risky steps</div>
            <div className="option-desc">
              Allow irreversible actions to run unattended (sub-account only).
            </div>
          </span>
        </label>

        <label className="option-row">
          <input
            type="checkbox"
            checked={options.allowDraft}
            onChange={() => toggle("allowDraft")}
            disabled={busy}
          />
          <span className="option-text">
            <div className="option-title">Allow draft replay</div>
            <div className="option-desc">
              Replay before the artifact is approved (normally blocked).
            </div>
          </span>
        </label>

        <div className="btn-row">
          <button className="btn" onClick={onDiscover} disabled={busy}>
            {busy === "discover" && <span className="spinner" />}
            Run discovery
          </button>
          <button
            className="btn btn-primary"
            onClick={onReplay}
            disabled={busy || !canReplay}
            title={!canReplay ? "Run discovery for this scenario first" : ""}
          >
            {busy === "replay" && <span className="spinner" />}
            Run replay
          </button>
        </div>
        {!canReplay && (
          <p className="panel-hint" style={{ marginTop: 10 }}>
            No artifact yet for this scenario — run discovery first.
          </p>
        )}
      </div>

      <div className="panel" style={{ marginTop: "auto" }}>
        <button className="btn btn-ghost" onClick={onReset} disabled={busy}>
          Reset demo data
        </button>
      </div>
    </>
  );
}
