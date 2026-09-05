const BASE = "/api";

async function req(path, opts) {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.error || body.reason || `request failed (${res.status})`);
  }
  return body;
}

export const api = {
  health: () => req("/health"),
  presets: () => req("/presets"),
  artifacts: () => req("/artifacts"),
  artifact: (id) => req(`/artifacts/${id}`),
  approve: (id) => req(`/artifacts/${id}/approve`, { method: "POST" }),
  discover: (body) => req("/discover", { method: "POST", body: JSON.stringify(body) }),
  replay: (body) => req("/replay", { method: "POST", body: JSON.stringify(body) }),
  runs: () => req("/runs"),
  run: (id) => req(`/runs/${id}`),
  reset: () => req("/reset", { method: "POST" }),
};
