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

// Answers a pending risky-step confirmation for a run that is currently paused
// (status === "awaiting_confirmation").
export function confirmRiskyStep(runId, approve) {
  return request(`/discover/${runId}/confirm`, {
    method: "POST",
    body: JSON.stringify({ approve }),
  });
}

export function listArtifacts() {
  return request("/artifacts");
}