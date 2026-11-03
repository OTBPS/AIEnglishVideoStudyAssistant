// Side panel: follows the active tab, shows its transcript, highlights the
// line being spoken, and seeks the video when a line is clicked.
import { api } from "./backend.js";
import { findSegmentIndex, formatTime } from "./lib/timeline.js";

const MANUAL_SCROLL_PAUSE_MS = 4000;

const $ = (id) => document.getElementById(id);
const el = {
  appStatus: $("app-status"),
  video: $("video"),
  title: $("video-title"),
  videoStatus: $("video-status"),
  hint: $("hint"),
  retry: $("retry"),
  list: $("transcript"),
};

let port = null;
let loadedKey = null; // "platform/externalId" whose transcript is rendered
let segments = [];
let currentIndex = -1;
let userScrolledAt = 0;

// ------------------------------------------------------------ app status
const APP_STATUS = {
  connected: (s) => [`已连接 ${s.version}`, "ok"],
  "version-mismatch": (s) => [`程序 ${s.version} / 扩展 ${s.extensionVersion} 版本不一致`, "warn"],
  incompatible: (s) => [`程序版本 ${s.version} 与扩展不兼容`, "warn"],
  offline: () => ["未连接", "err"],
};

async function refreshAppStatus() {
  let status;
  try {
    status = await chrome.runtime.sendMessage({ type: "status" });
  } catch {
    status = { state: "offline" };
  }
  const [text, cls] = (APP_STATUS[status?.state] || APP_STATUS.offline)(status || {});
  el.appStatus.textContent = text;
  el.appStatus.className = `muted ${cls}`;
  return status?.state;
}

// ------------------------------------------------------------ video state
const VIDEO_STATUS = {
  connecting: "正在连接桌面程序…",
  fetching: "正在获取英文字幕（视频开始播放后自动获取）…",
  ready: null, // filled from transcript info
  "no-english": "这个视频没有英文字幕。自动转写将在后续版本提供。",
  "transcript-error": "字幕获取失败，刷新页面可以重试。",
  "read-only": "学习数据来自更新版本的 VideoTutor，当前为只读。请升级桌面程序。",
  offline: "未找到 VideoTutor 桌面程序：请先启动它，然后刷新视频页面。",
  incompatible: "桌面程序与扩展版本不兼容，请把 VideoTutor 更新到最新版。",
  error: "桌面程序出错了，请查看托盘菜单里的日志。",
};

function showIdle(text) {
  el.video.hidden = true;
  el.list.hidden = true;
  el.hint.textContent = text;
  loadedKey = null;
  segments = [];
}

async function onState(state) {
  if (!state) return showIdle("打开一个 YouTube 英文视频，字幕会显示在这里。");
  el.hint.textContent = "";
  el.video.hidden = false;
  el.title.textContent = state.title || state.externalId;
  const t = state.transcript;
  el.videoStatus.textContent =
    state.status === "ready" && t
      ? `${t.segment_count} 句英文字幕 · ${t.kind === "manual" ? "人工字幕" : "自动生成字幕"}`
      : VIDEO_STATUS[state.status] || "";
  el.retry.hidden = state.status !== "offline";

  const key = `${state.platform}/${state.externalId}`;
  if (t && key !== loadedKey) await loadTranscript(state.platform, state.externalId, key);
  if (!t) {
    el.list.hidden = true;
    loadedKey = null;
  }
}

async function loadTranscript(platform, externalId, key) {
  const r = await api(`/api/videos/${encodeURIComponent(platform)}/${encodeURIComponent(externalId)}/transcript`);
  if (!r.ok) return;
  loadedKey = key;
  segments = r.data.segments;
  currentIndex = -1;
  el.list.replaceChildren(
    ...segments.map((s, i) => {
      const li = document.createElement("li");
      li.dataset.index = String(i);
      const time = document.createElement("time");
      time.textContent = formatTime(s.start_ms);
      const text = document.createElement("span");
      text.textContent = s.text; // textContent: transcript text is never parsed as HTML
      li.append(time, text);
      return li;
    }),
  );
  el.list.hidden = false;
}

function onTime(ms) {
  const i = findSegmentIndex(segments, ms);
  if (i === currentIndex || i < 0) return;
  el.list.children[currentIndex]?.classList.remove("current");
  currentIndex = i;
  const li = el.list.children[i];
  li?.classList.add("current");
  if (li && Date.now() - userScrolledAt > MANUAL_SCROLL_PAUSE_MS) {
    li.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

// ------------------------------------------------------------ tab binding
async function bindActiveTab() {
  port?.disconnect();
  port = null;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !/^https:\/\/www\.youtube\.com\//.test(tab.url || "")) {
    return showIdle("打开一个 YouTube 英文视频，字幕会显示在这里。");
  }
  try {
    port = chrome.tabs.connect(tab.id, { name: "sidepanel" });
  } catch {
    return showIdle("请刷新视频页面，让 VideoTutor 开始工作。");
  }
  port.onMessage.addListener((m) => {
    if (m.type === "state") onState(m.state);
    else if (m.type === "time") onTime(m.ms);
  });
  port.onDisconnect.addListener(() => {
    // No content script in that tab (opened before the extension was installed).
    if (chrome.runtime.lastError) showIdle("请刷新视频页面，让 VideoTutor 开始工作。");
    port = null;
  });
}

el.list.addEventListener("click", (ev) => {
  const li = ev.target.closest("li");
  if (!li || !port) return;
  const seg = segments[Number(li.dataset.index)];
  if (seg) port.postMessage({ type: "seek", ms: seg.start_ms });
});
for (const evName of ["wheel", "touchmove", "keydown"]) {
  window.addEventListener(evName, () => (userScrolledAt = Date.now()), { passive: true });
}
el.retry.addEventListener("click", async () => {
  if ((await refreshAppStatus()) === "connected") bindActiveTab();
});

chrome.tabs.onActivated.addListener(bindActiveTab);
chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (tab.active && info.status === "complete") bindActiveTab();
});

refreshAppStatus();
bindActiveTab();
