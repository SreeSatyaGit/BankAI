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

export function discover(targetUrl, goal) {
  return request("/discover", {
    method: "POST",
    body: JSON.stringify({ target_url: targetUrl, goal }),
  });
}

export function listArtifacts() {
  return request("/artifacts");
}
