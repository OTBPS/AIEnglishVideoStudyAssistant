"use strict";

async function getJson(url, init) {
  const resp = await fetch(url, init);
  if (!resp.ok) throw new Error(`${url} -> ${resp.status}`);
  return resp.json();
}

async function init() {
  const status = document.getElementById("status");
  try {
    const health = await getJson("/api/health");
    status.textContent = "本地服务运行中";
    status.className = "status ok";
    document.getElementById("footer").textContent = `${health.app} ${health.version}`;

    const info = await getJson("/api/setup/info");
    document.getElementById("ext-dir").textContent = info.extension_dir;

    document.getElementById("copy-ext").onclick = async (ev) => {
      await navigator.clipboard.writeText(info.extension_dir);
      ev.target.textContent = "已复制";
    };
    document.getElementById("open-ext").onclick = () =>
      fetch("/api/setup/open-extension-folder", { method: "POST" });

    if (info.database_read_only) {
      status.textContent = "学习数据来自更新版本的 VideoTutor，当前为只读。请安装最新版。";
      status.className = "status err";
    }
    renderRecent(await getJson("/api/videos/recent?limit=10"));
  } catch (err) {
    status.textContent = "无法连接本地服务，请确认 VideoTutor 正在运行（任务栏右下角托盘图标）。";
    status.className = "status err";
    console.error(err);
  }
}

function minutes(ms) {
  return Math.max(0, Math.round(ms / 60000));
}

function renderRecent(videos) {
  if (!videos.length) return;
  const list = document.getElementById("recent");
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
      const progress = v.duration_ms ? ` · 看到 ${Math.min(100, Math.round((100 * v.max_position_ms) / v.duration_ms))}%` : "";
      meta.textContent = `已观看 ${minutes(v.watched_ms)} 分钟${progress}${v.has_transcript ? "" : " · 无英文字幕"}`;
      li.append(a, meta);
      return li;
    }),
  );
  document.getElementById("recent-section").hidden = false;
}

init();
