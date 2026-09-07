import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";



export default function App() {
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);


  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" />
        </div>
      </header>


      <div className="center">
        {error && (
          <div style={{ padding: "14px 20px 0" }}>
            <div className="error-banner">{error}</div>
          </div>
        )}
        <SessionPanel result={result} />
      </div>
    </div>
  );
}
