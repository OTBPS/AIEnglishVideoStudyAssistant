// Service worker: owns the connection to the local app; UI pages ask it.
import { discover } from "./backend.js";

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

const BADGE = {
  connected: { text: "", color: "#16a34a" },
  incompatible: { text: "!", color: "#d97706" },
  offline: { text: "×", color: "#dc2626" },
};

async function refreshStatus() {
  const status = await discover();
  const badge = BADGE[status.state];
  await chrome.action.setBadgeText({ text: badge.text });
  await chrome.action.setBadgeBackgroundColor({ color: badge.color });
  return status;
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.type === "status") {
    refreshStatus().then(reply);
    return true; // async reply
  }
  return false;
});

refreshStatus();
