// Side panel: M0 shows only whether the desktop app is reachable and compatible.
import { PRODUCT } from "./product.js";

const MESSAGES = {
  connected: (s) => [`已连接 VideoTutor ${s.version}`, "打开 YouTube 或 Bilibili 的英文视频即可开始（后续版本提供）。"],
  incompatible: (s) => [
    `桌面程序版本 ${s.version} 与扩展不兼容`,
    `扩展协议 ${PRODUCT.protocol}，程序协议 ${s.protocol}。请把两者都更新到最新版。`,
  ],
  offline: () => ["未找到 VideoTutor 桌面程序", "请先启动 VideoTutor（开始菜单或桌面图标），然后点“重新连接”。"],
};

async function refresh() {
  const statusEl = document.getElementById("status");
  const hintEl = document.getElementById("hint");
  const retry = document.getElementById("retry");
  statusEl.textContent = "正在连接桌面程序…";
  statusEl.className = "muted";
  retry.hidden = true;

  const status = await chrome.runtime.sendMessage({ type: "status" });
  const [title, hint] = MESSAGES[status.state](status);
  statusEl.textContent = title;
  statusEl.className = status.state;
  hintEl.textContent = hint;
  retry.hidden = status.state === "connected";
}

document.getElementById("retry").addEventListener("click", refresh);
refresh();
