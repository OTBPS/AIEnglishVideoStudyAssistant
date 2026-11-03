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
    await Promise.all([setupLlm(), setupLevel()]);
    renderRecent(await getJson("/api/videos/recent?limit=10"));
  } catch (err) {
    status.textContent = "无法连接本地服务，请确认 VideoTutor 正在运行（任务栏右下角托盘图标）。";
    status.className = "status err";
    console.error(err);
  }
}

init();
