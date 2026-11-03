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
  } catch (err) {
    status.textContent = "无法连接本地服务，请确认 VideoTutor 正在运行（任务栏右下角托盘图标）。";
    status.className = "status err";
    console.error(err);
  }
}

init();
