// Finding and talking to the local VideoTutor app.
//
// The app binds the first free port in [portFirst, portLast] (product.toml);
// we probe the same range and remember the last port that answered.
import { PRODUCT } from "./product.js";

const PROBE_TIMEOUT_MS = 800;

async function probe(port) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), PROBE_TIMEOUT_MS);
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/api/health`, { signal: ctrl.signal });
    if (!resp.ok) return null;
    const health = await resp.json();
    return health.app === PRODUCT.name ? { port, ...health } : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// Returns {state: "connected" | "incompatible" | "offline", port?, version?, protocol?}.
export async function discover() {
  const { lastPort } = await chrome.storage.local.get("lastPort");
  const ports = [];
  if (Number.isInteger(lastPort)) ports.push(lastPort);
  for (let p = PRODUCT.portFirst; p <= PRODUCT.portLast; p++) {
    if (p !== lastPort) ports.push(p);
  }
  for (const port of ports) {
    const found = await probe(port);
    if (!found) continue;
    await chrome.storage.local.set({ lastPort: port });
    const state = found.protocol === PRODUCT.protocol ? "connected" : "incompatible";
    return { state, port, version: found.version, protocol: found.protocol };
  }
  return { state: "offline" };
}
