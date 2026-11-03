"use strict";

const $ = (id) => document.getElementById(id);

async function getJson(url, init) {
  const resp = await fetch(url, init);
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    const err = new Error(body?.detail || `${url} -> ${resp.status}`);
    err.status = resp.status;
    throw err;
  }
  return body;
}

const sendJson = (url, method, data) =>
  getJson(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

function showResult(el, text, ok) {
  el.textContent = text;
  el.className = `result ${ok ? "ok" : "err"}`;
}

// ------------------------------------------------------------ step 1: extension
function setupExtension(info) {
  $("ext-dir").textContent = info.extension_dir;
  $("copy-ext").onclick = async (ev) => {
    await navigator.clipboard.writeText(info.extension_dir);
    ev.target.textContent = "已复制";
  };
  $("open-ext").onclick = () => fetch("/api/setup/open-extension-folder", { method: "POST" });
}

// ------------------------------------------------------------ step 2: AI key
const DEFAULT_MODELS = { claude: "claude-opus-5-5", openai_compatible: "" };

function provider() {
  return document.querySelector('input[name="provider"]:checked').value;
}

function renderLlm(config) {
  document.querySelector(`input[name="provider"][value="${config.provider}"]`).checked = true;
  $("llm-model").value = config.model;
  $("llm-base-url").value = config.base_url;
  $("base-url-field").hidden = config.provider !== "openai_compatible";
  $("base-url-note").hidden = config.provider !== "openai_compatible";
  $("model-note").hidden = config.provider !== "claude";
  $("llm-key").placeholder = config.has_key ? "已保存（留空则不修改）" : "粘贴密钥";
  $("llm-remove").hidden = !config.has_key;
}

async function setupLlm() {
  renderLlm(await getJson("/api/settings/llm"));
  for (const radio of document.querySelectorAll('input[name="provider"]')) {
    radio.onchange = () => {
      const p = provider();
      $("base-url-field").hidden = p !== "openai_compatible";
      $("base-url-note").hidden = p !== "openai_compatible";
      $("model-note").hidden = p !== "claude";
      $("llm-model").value = DEFAULT_MODELS[p];
    };
  }
  $("llm-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const result = $("llm-result");
    const key = $("llm-key").value.trim();
    showResult(result, "正在保存并测试…", true);
    $("llm-save").disabled = true;
    try {
      const r = await sendJson("/api/settings/llm", "PUT", {
        provider: provider(),
        model: $("llm-model").value.trim(),
        base_url: $("llm-base-url").value.trim(),
        api_key: key || null,
      });
      $("llm-key").value = "";
      renderLlm(r.config);
      showResult(result, r.ok ? "连接成功，AI 已可以使用。" : r.error, r.ok);
    } catch (err) {
      showResult(result, err.message, false);
    } finally {
      $("llm-save").disabled = false;
    }
  };
  $("llm-remove").onclick = async () => {
    renderLlm(await getJson("/api/settings/llm/key", { method: "DELETE" }));
    showResult($("llm-result"), "已删除保存的密钥。", true);
  };
}

// ------------------------------------------------------------ step 3: vocabulary level
async function setupLevel() {
  const s = await getJson("/api/settings");
  const select = $("vocab-level");
  if (![...select.options].some((o) => Number(o.value) === s.vocabulary_level)) {
    select.add(new Option(`约 ${s.vocabulary_level} 词`, String(s.vocabulary_level)));
  }
  select.value = String(s.vocabulary_level);
  $("auto-prewatch").checked = s.auto_prewatch;
  const save = async (data) => {
    try {
      await sendJson("/api/settings", "PUT", data);
      showResult($("level-result"), "已保存", true);
    } catch (err) {
      showResult($("level-result"), err.message, false);
    }
  };
  select.onchange = () => save({ vocabulary_level: Number(select.value) });
  $("auto-prewatch").onchange = (ev) => save({ auto_prewatch: ev.target.checked });
}

// ------------------------------------------------------------ step 4: speech recognition
const SPEECH_POLL_MS = 1500;
let speechTimer = null;

function gb(bytes) {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "text") node.textContent = v; // never innerHTML
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v);
  }
  node.append(...children.filter(Boolean));
  return node;
}

function hardwareText(s) {
  const g = s.gpu;
  const using = s.ready ? ` 现在会用「${s.model}」在 ${s.device === "cuda" ? "显卡" : "CPU"} 上识别。` : "";
  if (!g.name) return `没有检测到 NVIDIA 显卡，识别会用 CPU（标准模型识别 10 分钟的视频大约要 2 分钟）。${using}`;
  const mem = g.memory_mb ? `，${(g.memory_mb / 1024).toFixed(0)} GB 显存` : "";
  const fail = g.failure ? ` 上次显卡加速没能运行，已改用 CPU（${g.failure.slice(0, 120)}）。` : "";
  return `检测到 NVIDIA 显卡：${g.name}${mem}。${fail}${using}`;
}

const ACTIONS = {
  install: (id) => sendJson(`/api/speech/components/${encodeURIComponent(id)}/install`, "POST", {}),
  cancel: (id) => sendJson(`/api/speech/components/${encodeURIComponent(id)}/cancel`, "POST", {}),
  remove: (id) => getJson(`/api/speech/components/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

function componentItem(c, gpuName) {
  const act = (name) => async (ev) => {
    ev.target.disabled = true;
    try {
      await ACTIONS[name](c.id);
    } catch (err) {
      alert(err.message);
    }
    refreshSpeech();
  };
  let state;
  if (c.status === "installing") {
    state = [
      el("progress", { max: String(c.download_bytes), value: String(c.downloaded_bytes) }),
      el("div", { class: "actions" },
        el("span", { class: "state", text: `正在下载 ${gb(c.downloaded_bytes)} / ${gb(c.download_bytes)}` }),
        el("button", { class: "secondary", text: "取消", onclick: act("cancel") })),
    ];
  } else if (c.status === "installed") {
    state = [el("div", { class: "actions" },
      el("span", { class: "state result ok", text: "已安装" }),
      el("button", { class: "secondary", text: "删除", onclick: act("remove") }))];
  } else {
    state = [el("div", { class: "actions" },
      el("button", { text: c.downloaded_bytes ? "继续安装" : "安装", onclick: act("install") }),
      c.error && el("span", { class: "state result err", text: c.error }))];
  }
  const note = c.kind === "gpu_runtime" && !gpuName ? "（没有检测到 NVIDIA 显卡，不需要安装）" : "";
  return el("li", {},
    el("div", { class: "head" },
      el("strong", { text: c.title }),
      el("span", { class: "size", text: `下载 ${gb(c.download_bytes)}${note}` }),
      c.recommended && el("span", { class: "badge", text: "推荐" })),
    el("div", { class: "desc", text: c.description }),
    ...state);
}

async function refreshSpeech() {
  clearTimeout(speechTimer);
  const s = await getJson("/api/speech");
  $("speech-hw").textContent = hardwareText(s);
  // Without an NVIDIA GPU the runtime would be 1.3 GB of nothing: list it only when useful.
  const shown = s.components.filter((c) => c.kind !== "gpu_runtime" || s.gpu.name || c.status !== "not_installed");
  $("speech-components").replaceChildren(...shown.map((c) => componentItem(c, s.gpu.name)));
  if (s.components.some((c) => c.status === "installing")) speechTimer = setTimeout(refreshSpeech, SPEECH_POLL_MS);
}

// ------------------------------------------------------------ review
async function setupReview() {
  const s = await getJson("/api/review/summary");
  if (!s.total) return; // nothing collected yet: the section would only be noise
  $("review-summary").textContent =
    `待复习 ${s.due} 个 · 共 ${s.total} 个单词在学 · 今天已练习 ${s.reviewed_today} 次`;
  $("review-start").textContent = s.due ? `开始复习（${s.due}）` : "提前练习";
  $("review-section").hidden = false;
}

// ------------------------------------------------------------ recent videos
function minutes(ms) {
  return Math.max(0, Math.round(ms / 60000));
}

function renderRecent(videos) {
  if (!videos.length) return;
  const list = $("recent");
  list.replaceChildren(
    ...videos.map((v) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = v.url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = v.title || v.external_id; // textContent: titles are never parsed as HTML
      const meta = document.createElement("span");
      meta.className = "meta";
      const progress = v.duration_ms
        ? ` · 看到 ${Math.min(100, Math.round((100 * v.max_position_ms) / v.duration_ms))}%`
        : "";
      meta.textContent = `已观看 ${minutes(v.watched_ms)} 分钟${progress}${v.has_transcript ? "" : " · 无英文字幕"}`;
      li.append(a, meta);
      return li;
    }),
  );
  $("recent-section").hidden = false;
}

// ------------------------------------------------------------ startup
async function init() {
  const status = $("status");
  try {
    const health = await getJson("/api/health");
    status.textContent = "本地服务运行中";
    status.className = "status ok";
    $("footer").textContent = `${health.app} ${health.version}`;

    const info = await getJson("/api/setup/info");
    setupExtension(info);
    if (info.database_read_only) {
      status.textContent = "学习数据来自更新版本的 VideoTutor，当前为只读。请安装最新版。";
      status.className = "status err";
    }
    await Promise.all([setupLlm(), setupLevel(), setupReview(), refreshSpeech()]);
    renderRecent(await getJson("/api/videos/recent?limit=10"));
  } catch (err) {
    status.textContent = "无法连接本地服务，请确认 VideoTutor 正在运行（任务栏右下角托盘图标）。";
    status.className = "status err";
    console.error(err);
  }
}

init();
