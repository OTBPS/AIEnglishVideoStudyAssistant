// Finding and talking to the local VideoTutor app.
//
// The app binds the first free port in [portFirst, portLast] (product.toml);
// we probe the same range and remember the port that answered. Used by the
// service worker and the side panel (both are extension pages, so requests to
// 127.0.0.1 carry the extension's Origin, which the app trusts).
import { PRODUCT } from "./product.js";

const PROBE_TIMEOUT_MS = 800;
const REQUEST_TIMEOUT_MS = 15000;

let knownPort = null;

async function fetchWithTimeout(url, init, timeoutMs) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function probe(port) {
  try {
    const resp = await fetchWithTimeout(`http://127.0.0.1:${port}/api/health`, {}, PROBE_TIMEOUT_MS);
    if (!resp.ok) return null;
    const health = await resp.json();
    return health.app === PRODUCT.name ? { port, ...health } : null;
  } catch {
    return null;
  }
}

// {state: "connected" | "incompatible" | "offline", port?, version?, protocol?}
export async function discover() {
  const { lastPort } = await chrome.storage.local.get("lastPort");
  const ports = [];
  for (const p of [knownPort, lastPort]) if (Number.isInteger(p) && !ports.includes(p)) ports.push(p);
  for (let p = PRODUCT.portFirst; p <= PRODUCT.portLast; p++) if (!ports.includes(p)) ports.push(p);
  for (const port of ports) {
    const found = await probe(port);
    if (!found) continue;
    knownPort = port;
    await chrome.storage.local.set({ lastPort: port });
    const state = found.protocol === PRODUCT.protocol ? "connected" : "incompatible";
    return { state, port, version: found.version, protocol: found.protocol };
  }
  knownPort = null;
  return { state: "offline" };
}

// Call the app. Resolves to {ok: true, data} or {ok: false, status, detail};
// never throws, so callers can show a state instead of crashing.
export async function api(path, { method = "GET", body } = {}) {
  for (let attempt = 0; attempt < 2; attempt++) {
    if (knownPort === null) {
      const found = await discover();
      if (found.state !== "connected") return { ok: false, status: found.state };
    }
    try {
      const resp = await fetchWithTimeout(
        `http://127.0.0.1:${knownPort}${path}`,
        {
          method,
          headers: body === undefined ? {} : { "Content-Type": "application/json" },
          body: body === undefined ? undefined : JSON.stringify(body),
        },
        REQUEST_TIMEOUT_MS,
      );
      const data = await resp.json().catch(() => null);
      if (resp.ok) return { ok: true, data };
      return { ok: false, status: resp.status === 409 ? "read-only" : "error", detail: data?.detail };
    } catch {
      knownPort = null; // app restarted on another port, or quit: rediscover once
    }
  }
  return { ok: false, status: "offline" };
}
