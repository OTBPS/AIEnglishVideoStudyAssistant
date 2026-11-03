// The "no English subtitles" card: make them with local speech recognition.
import { h } from "./dom.js";

const STAGE = {
  queued: "排队中（一次只识别一个视频）",
  downloading: "正在下载音频…",
  recognizing: "正在识别语音",
};
const DEVICE = { cuda: "NVIDIA 显卡", cpu: "CPU" };

function percent(p) {
  return `${Math.round(100 * (p || 0))}%`;
}

// state: the content script's snapshot; speech: GET /api/speech (or null when unknown).
export function renderSpeech(el, state, speech, handlers) {
  const show = state && !state.transcript && ["no-english", "transcribing"].includes(state.status);
  el.hidden = !show;
  if (!show) {
    el.replaceChildren();
    return;
  }
  if (!state.canTranscribe) {
    el.replaceChildren(h("p", { class: "muted", text: "这个视频没有英文字幕，可能不是英语视频。" }));
    return;
  }
  const job = state.job;
  if (state.status === "transcribing" && job) {
    const stage = STAGE[job.status] || "正在准备…";
    const detail = job.status === "recognizing" ? ` ${percent(job.progress)}` : "";
    const device = job.device ? ` · ${DEVICE[job.device] || job.device}` : "";
    el.replaceChildren(
      h("h2", { text: "正在生成英文字幕" }),
      h("p", { text: `${stage}${detail}${device}` }),
      h("progress", { max: "1", value: job.status === "recognizing" ? String(job.progress || 0) : undefined }),
      h("p", { class: "muted", text: "可以继续看视频，识别完成后字幕会自动出现。" }),
      h("button", { class: "small secondary", text: "取消", onclick: handlers.cancel }),
    );
    return;
  }
  const parts = [h("h2", { text: "用语音识别生成字幕" })];
  if (job?.status === "failed" && job.error) parts.push(h("p", { class: "err", text: job.error }));
  if (!speech) {
    parts.push(h("p", { class: "muted", text: "正在检查语音识别是否可用…" }));
  } else if (speech.ready) {
    const device = DEVICE[speech.device] || speech.device;
    const model = speech.components.find((c) => c.id === speech.model)?.title || speech.model;
    parts.push(
      h("p", { text: "可以在你的电脑上识别语音，生成英文字幕（不上传、不收费）。" }),
      h("button", { text: job ? "重新识别" : "生成英文字幕", onclick: handlers.transcribe }),
      h("p", { class: "muted", text: `${model} · ${device}` }),
    );
  } else {
    parts.push(
      h("p", { text: "安装语音识别模型后，就能为这类视频生成英文字幕。" }),
      h("button", { text: "去主页安装", onclick: handlers.openSetup }),
    );
  }
  el.replaceChildren(...parts);
}
