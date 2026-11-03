// Side panel controller: follows the active tab, loads transcript + vocabulary +
// pre-watch material from the desktop app, highlights the line being spoken,
// and lets the learner look up and mark words.
import { api } from "./backend.js";
import { renderPrewatch, renderSummary } from "./panel/prewatch.js";
import { openWordPopover, renderTranscript, wordClass } from "./panel/transcript.js";
import { findSegmentIndex } from "./lib/timeline.js";

const MANUAL_SCROLL_PAUSE_MS = 4000;
const PREWATCH_POLL_MS = 2500;

const $ = (id) => document.getElementById(id);
const el = {
  appStatus: $("app-status"),
  video: $("video"),
  title: $("video-title"),
  videoStatus: $("video-status"),
  summary: $("summary"),
  prewatch: $("prewatch"),
  hint: $("hint"),
  retry: $("retry"),
  list: $("transcript"),
  pop: $("popover"),
};

let port = null;
let current = null; // {platform, externalId, key}
let segments = [];
let vocab = null;
let prewatch = null;
let currentIndex = -1;
let userScrolledAt = 0;
let pollTimer = null;
const glossCache = new Map();

const enc = encodeURIComponent;
const videoPath = () => `/api/videos/${enc(current.platform)}/${enc(current.externalId)}`;

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
  el.pop.hidden = true;
  el.hint.textContent = text;
  current = null;
  segments = [];
  vocab = null;
  prewatch = null;
  clearTimeout(pollTimer);
  renderSummary(el.summary, null);
  renderPrewatch(el.prewatch, null);
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
  if (!t) {
    el.list.hidden = true;
    return;
  }
  if (current?.key !== key) {
    current = { platform: state.platform, externalId: state.externalId, key };
    glossCache.clear();
    await loadVideo();
  }
}

async function loadVideo() {
  const [t, v] = await Promise.all([api(`${videoPath()}/transcript`), api(`${videoPath()}/vocabulary`)]);
  if (!t.ok) return;
  segments = t.data.segments;
  vocab = v.ok ? v.data : null;
  currentIndex = -1;
  renderTranscript(el.list, segments, vocab);
  renderSummary(el.summary, vocab);
  el.list.hidden = false;
  await loadPrewatch();
}

async function refreshVocabulary() {
  const v = await api(`${videoPath()}/vocabulary`);
  if (!v.ok) return;
  vocab = v.data;
  renderSummary(el.summary, vocab);
  // Restyle words in place: rebuilding the list would lose the scroll position.
  for (const span of el.list.querySelectorAll("span[data-word]")) {
    span.className = wordClass(vocab.words[Number(span.dataset.word)]);
  }
}

// ------------------------------------------------------------ pre-watch
async function loadPrewatch() {
  clearTimeout(pollTimer);
  if (!current) return;
  const r = await api(`${videoPath()}/prewatch`);
  if (!r.ok) return;
  prewatch = r.data;
  for (const w of prewatch.words) if (w.gloss) glossCache.set(w.word.lemma, w.gloss);
  renderPrewatch(el.prewatch, prewatch, { mark, generate });
  if (prewatch.status === "running") pollTimer = setTimeout(loadPrewatch, PREWATCH_POLL_MS);
}

async function generate() {
  const r = await api(`${videoPath()}/prewatch`, { method: "POST", body: { auto: false } });
  if (r.ok) {
    prewatch = r.data;
    renderPrewatch(el.prewatch, prewatch, { mark, generate });
  }
  pollTimer = setTimeout(loadPrewatch, PREWATCH_POLL_MS);
}

// ------------------------------------------------------------ words
async function mark(lemma, status) {
  el.pop.hidden = true;
  const r = await api(`/api/words/${enc(lemma)}`, { method: "PUT", body: { status } });
  if (!r.ok) return;
  await Promise.all([refreshVocabulary(), loadPrewatch()]);
}

async function lookup(lemma) {
  if (glossCache.has(lemma)) return glossCache.get(lemma);
  const r = await api(`${videoPath()}/glosses`, { method: "POST", body: { lemma } });
  if (!r.ok) throw new Error(r.detail || "查询失败");
  glossCache.set(lemma, r.data);
  return r.data;
}

// ------------------------------------------------------------ playback sync
function onTime(ms) {
  const i = findSegmentIndex(segments, ms);
  if (i === currentIndex || i < 0) return;
  el.list.children[currentIndex]?.classList.remove("current");
  currentIndex = i;
  const li = el.list.children[i];
  li?.classList.add("current");
  if (li && el.pop.hidden && Date.now() - userScrolledAt > MANUAL_SCROLL_PAUSE_MS) {
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
    else if (m.type === "prewatch-started") loadPrewatch();
  });
  port.onDisconnect.addListener(() => {
    if (chrome.runtime.lastError) showIdle("请刷新视频页面，让 VideoTutor 开始工作。");
    port = null;
  });
}

el.list.addEventListener("click", (ev) => {
  const wordEl = ev.target.closest("span[data-word]");
  if (wordEl && vocab) {
    const word = vocab.words[Number(wordEl.dataset.word)];
    openWordPopover(el.pop, wordEl, word, {
      mark,
      lookup,
      canLookup: prewatch?.status !== "not_configured",
      autoLookup: glossCache.has(word.lemma),
    });
    return;
  }
  el.pop.hidden = true;
  const li = ev.target.closest("li");
  const seg = li && segments[Number(li.dataset.index)];
  if (seg && port) port.postMessage({ type: "seek", ms: seg.start_ms });
});
document.addEventListener("click", (ev) => {
  if (!el.pop.hidden && !el.pop.contains(ev.target) && !ev.target.closest("span[data-word]")) {
    el.pop.hidden = true;
  }
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") el.pop.hidden = true;
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
