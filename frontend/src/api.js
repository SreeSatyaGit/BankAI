const BASE = "/api";

async function request(path, options) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let body = null;
  try {
    body = await res.json();
  } catch {
    // no body / not JSON — fine for e.g. a bare 501
  }
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : res.statusText;
    throw new Error(`${res.status} ${detail}`);
  }
  return body;
}

export function health() {
  return request("/health");
}

// Starts a run in the background; returns {run_id, status} immediately. Poll
// getRunStatus(run_id) for progress — discovery can pause mid-run on a risky
// step, so this can no longer be a single request/response round trip.
export function startDiscovery(targetUrl, goal) {
  return request("/discover", {
    method: "POST",
    body: JSON.stringify({ target_url: targetUrl, goal }),
  });
}

export function getRunStatus(runId) {
  return request(`/discover/${runId}`);
}

// Resolves a pending escalation (risky step / stuck / retry-exhausted) for a
// run currently paused (status === "awaiting_intervention"). `decision` is
// one of "approve" | "skip" | "continue" | "retry" | "abort" — which ones are
// meaningful depends on the trigger, see the intervention panel in App.jsx.
// (A planner whose API call itself fails is NOT an escalation — the run just
// ends; there's nothing to resume here for that.)
export function resumeRun(runId, decision, note) {
  return request(`/discover/${runId}/resume`, {
    method: "POST",
    body: JSON.stringify({ decision, note: note || null }),
  });
}

// Lets a human act directly on the SAME live session a paused run is using —
// click, type, navigate, extract — without resuming the agent yet. Can be
// called any number of times while paused. `action` matches the backend's
// Action schema: {type, locator?, value?, output_name?}.
export function submitManualAction(runId, action, description) {
  return request(`/discover/${runId}/manual-action`, {
    method: "POST",
    body: JSON.stringify({ action, description: description || null }),
  });
}

export function listArtifacts() {
  return request("/artifacts");
}
