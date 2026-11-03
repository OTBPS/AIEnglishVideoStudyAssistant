// Service worker: the only part of the extension that writes to the app.
// Content scripts cannot call 127.0.0.1 themselves (they run under the page's
// origin), so they send messages here.
import { api, discover } from "./backend.js";
import { PRODUCT } from "./product.js";

const RELOAD_GUARD_MS = 10 * 60 * 1000;

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

const BADGE = {
  connected: { text: "", color: "#16a34a" },
  incompatible: { text: "!", color: "#d97706" },
  "version-mismatch": { text: "!", color: "#d97706" },
  offline: { text: "×", color: "#dc2626" },
};

// After the desktop app is upgraded, its installer has already replaced this
// extension's files on disk, but Chrome keeps running the old code until the
// extension reloads. Reload once per new version; if that does not help (the
// user loaded the extension from another folder), report the mismatch.
async function reconcileVersion(status) {
  if (status.state !== "connected" || status.version === PRODUCT.version) return status;
  const { reloadedFor } = await chrome.storage.local.get("reloadedFor");
  const recent = reloadedFor?.version === status.version && Date.now() - reloadedFor.at < RELOAD_GUARD_MS;
  if (!recent) {
    await chrome.storage.local.set({ reloadedFor: { version: status.version, at: Date.now() } });
    chrome.runtime.reload();
  }
  return { ...status, state: "version-mismatch", extensionVersion: PRODUCT.version };
}

async function refreshStatus() {
  const status = await reconcileVersion(await discover());
  const badge = BADGE[status.state];
  await chrome.action.setBadgeText({ text: badge.text });
  await chrome.action.setBadgeBackgroundColor({ color: badge.color });
  return status;
}

// ------------------------------------------------------------ open sessions per tab
// Kept in storage.session: a service worker can be stopped at any time.
async function rememberSession(tabId, sessionId) {
  const { sessions = {} } = await chrome.storage.session.get("sessions");
  sessions[tabId] = sessionId;
  await chrome.storage.session.set({ sessions });
}

async function forgetSession(sessionId) {
  const { sessions = {} } = await chrome.storage.session.get("sessions");
  for (const [tab, sid] of Object.entries(sessions)) if (sid === sessionId) delete sessions[tab];
  await chrome.storage.session.set({ sessions });
}

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const { sessions = {} } = await chrome.storage.session.get("sessions");
  const sid = sessions[tabId];
  if (sid) {
    await api(`/api/sessions/${sid}/close`, { method: "POST" });
    await forgetSession(sid);
  }
});

// ------------------------------------------------------------ messages
const enc = encodeURIComponent;

const handlers = {
  status: () => refreshStatus(),

  "open-video": async (msg, sender) => {
    const r = await api("/api/videos/open", { method: "POST", body: msg.video });
    if (r.ok && sender.tab?.id !== undefined) await rememberSession(sender.tab.id, r.data.session_id);
    return r;
  },

  "store-captions": (msg) =>
    api(`/api/videos/${enc(msg.platform)}/${enc(msg.externalId)}/captions`, {
      method: "PUT",
      body: msg.payload,
    }),

  "start-prewatch": (msg) =>
    api(`/api/videos/${enc(msg.platform)}/${enc(msg.externalId)}/prewatch`, {
      method: "POST",
      body: { auto: true },
    }),

  "watch-event": (msg) =>
    api(`/api/sessions/${Number(msg.sessionId)}/events`, { method: "POST", body: { events: [msg.event] } }),

  "close-session": async (msg) => {
    const r = await api(`/api/sessions/${Number(msg.sessionId)}/close`, { method: "POST" });
    await forgetSession(msg.sessionId);
    return r;
  },
};

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const handler = handlers[msg?.type];
  if (!handler) return false;
  Promise.resolve(handler(msg, sender))
    .then(reply)
    .catch((err) => reply({ ok: false, status: "error", detail: String(err) }));
  return true; // async reply
});

refreshStatus();
