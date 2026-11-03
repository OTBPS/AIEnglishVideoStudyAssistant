// Isolated-world content script on video pages (YouTube, Bilibili).
//
// Owns the per-tab watching state: which video, which watch session, whether
// its transcript is stored. Forwards the page script's findings to the service
// worker, reports playback events, replays missed lines (Alt+R), runs local
// speech recognition for videos without English subtitles, and serves the
// side panel over a Port ("sidepanel"): state + current time out, seek /
// "missed" / "transcribe" requests in. Platform differences live in
// PLATFORMS, nowhere else.
(() => {
  "use strict";
  const FROM_PAGE = "videotutor-page";
  const FROM_CONTENT = "videotutor-content";
  const PROGRESS_EVERY_MS = 5000;
  const NO_TRANSCRIPT_REWIND_MS = 5000;
  const TRANSCRIPTION_POLL_MS = 3000;

  const PLATFORMS = {
    "www.youtube.com": {
      name: "youtube",
      video: () => document.querySelector("#movie_player video") || document.querySelector("video"),
      isAd: () => Boolean(document.getElementById("movie_player")?.classList.contains("ad-showing")),
      // Its audio streams need tokens only the player can mint; and a YouTube
      // video without an English track is almost never in English anyway.
      canTranscribe: false,
    },
    "www.bilibili.com": {
      name: "bilibili",
      video: () => document.querySelector("#bilibili-player video") || document.querySelector("video"),
      isAd: () => false,
      canTranscribe: true, // bilibili/page.js reads the audio stream URL
    },
  };
  const platform = PLATFORMS[location.hostname];
  if (!platform) return;

  let state = null; // {videoId, title, sessionId, transcript, wanted, status, job}
  let video = null;
  let lastProgressAt = 0;
  let jobTimer = null;
  const panels = new Set();

  const toPage = (msg) => window.postMessage({ source: FROM_CONTENT, ...msg }, location.origin);
  const toPanels = (msg) => {
    for (const port of panels) port.postMessage(msg);
  };

  async function send(msg) {
    try {
      return (await chrome.runtime.sendMessage(msg)) ?? { ok: false, status: "offline" };
    } catch {
      return { ok: false, status: "offline" }; // extension reloaded or worker unavailable
    }
  }

  function snapshot() {
    return {
      type: "state",
      state: state && {
        platform: platform.name,
        externalId: state.videoId,
        title: state.title,
        status: state.status,
        transcript: state.transcript,
        canTranscribe: platform.canTranscribe,
        job: state.job, // speech recognition: {status, progress, error, model, device} or null
      },
    };
  }

  const notifyPanels = () => toPanels(snapshot());

  function setStatus(status) {
    if (state) state.status = status;
    notifyPanels();
  }

  // ------------------------------------------------------------ video lifecycle
  async function onVideo(v) {
    await leave();
    state = {
      videoId: v.videoId,
      title: v.title,
      sessionId: null,
      transcript: null,
      wanted: null,
      status: "connecting",
      job: null,
    };
    notifyPanels();
    const r = await send({
      type: "open-video",
      video: { platform: platform.name, external_id: v.videoId, title: v.title, url: v.url, duration_ms: v.durationMs, tracks: v.tracks },
    });
    if (state?.videoId !== v.videoId) return; // navigated away meanwhile
    if (!r.ok) return setStatus(r.status || "offline");
    state.sessionId = r.data.session_id;
    state.transcript = r.data.transcript;
    state.wanted = r.data.wanted_track;
    attachVideo();
    if (state.wanted) requestTrack();
    setStatus(state.transcript ? "ready" : state.wanted ? "fetching" : "no-english");
    if (state.transcript) startPrewatch();
    else if (!state.wanted && platform.canTranscribe) resumeTranscription(v.videoId);
    if (video && !video.paused) watchEvent("play");
  }

  function requestTrack() {
    if (state?.wanted) toPage({ type: "fetch-track", videoId: state.videoId, track: state.wanted });
  }

  async function onTrackData(m) {
    if (!state || m.videoId !== state.videoId) return;
    const r = await send({
      type: "store-captions",
      platform: platform.name,
      externalId: m.videoId,
      payload: { track: m.track, format: m.format, body: m.body },
    });
    if (!state || m.videoId !== state.videoId) return;
    if (r.ok) {
      state.transcript = r.data;
      state.wanted = null;
      setStatus("ready");
      startPrewatch();
    } else if (!state.transcript) {
      setStatus("transcript-error");
    }
  }

  // Ask the app to prepare glosses and questions in the background; it decides
  // (auto_prewatch setting, AI key present, already done) whether to act.
  async function startPrewatch() {
    if (!state) return;
    const r = await send({ type: "start-prewatch", platform: platform.name, externalId: state.videoId });
    if (r.ok) toPanels({ type: "prewatch-started" });
  }

  async function leave() {
    clearTimeout(jobTimer);
    if (state?.sessionId) send({ type: "close-session", sessionId: state.sessionId });
    state = null;
    notifyPanels();
  }

  // ------------------------------------------------------------ speech recognition
  // The job runs in the desktop app and survives this page: reopening the
  // video picks it up again.
  const ids = (videoId) => ({ platform: platform.name, externalId: videoId });

  async function resumeTranscription(videoId) {
    const r = await send({ type: "transcription", ...ids(videoId) });
    if (r.ok && r.data && !["failed", "cancelled"].includes(r.data.status)) onJob(videoId, r.data);
  }

  function transcribe() {
    if (!state || state.transcript || !platform.canTranscribe) return;
    if (state.job && !["failed", "cancelled"].includes(state.job.status)) return; // already running
    state.job = { status: "queued", progress: 0 };
    setStatus("transcribing");
    toPage({ type: "audio-source", videoId: state.videoId });
  }

  async function onAudioSource(m) {
    if (state?.videoId !== m.videoId) return;
    const r = await send({ type: "transcribe", ...ids(m.videoId), urls: m.urls, durationMs: m.durationMs });
    if (r.ok) onJob(m.videoId, r.data);
    else jobFailed(m.videoId, r.detail || "无法开始语音识别，请确认 VideoTutor 正在运行。");
  }

  function jobFailed(videoId, error) {
    if (state?.videoId !== videoId) return;
    state.job = { status: "failed", progress: 0, error };
    setStatus("no-english");
  }

  function onJob(videoId, job) {
    if (state?.videoId !== videoId) return;
    clearTimeout(jobTimer);
    state.job = job;
    if (job.status === "done" && job.transcript) {
      state.transcript = job.transcript;
      setStatus("ready");
      startPrewatch();
    } else if (job.status === "failed" || job.status === "cancelled") {
      setStatus("no-english");
    } else {
      setStatus("transcribing");
      jobTimer = setTimeout(() => pollJob(videoId), TRANSCRIPTION_POLL_MS);
    }
  }

  async function pollJob(videoId) {
    if (state?.videoId !== videoId) return;
    const r = await send({ type: "transcription", ...ids(videoId) });
    if (r.ok && r.data) onJob(videoId, r.data);
    else if (r.ok) jobFailed(videoId, "桌面程序重启过，识别已中断，请重试。");
    else jobTimer = setTimeout(() => pollJob(videoId), TRANSCRIPTION_POLL_MS * 2); // app busy or restarting
  }

  async function cancelTranscription() {
    if (!state?.job) return;
    const videoId = state.videoId;
    const r = await send({ type: "cancel-transcription", ...ids(videoId) });
    if (r.ok && r.data) onJob(videoId, r.data);
  }

  // ------------------------------------------------------------ playback
  function attachVideo() {
    const v = platform.video();
    if (!v || v === video) return;
    video = v;
    v.addEventListener("play", () => watchEvent("play"));
    v.addEventListener("pause", () => watchEvent("pause"));
    v.addEventListener("seeked", () => watchEvent("seek"));
    v.addEventListener("ratechange", () => watchEvent("rate", v.playbackRate));
    v.addEventListener("ended", () => watchEvent("ended"));
    v.addEventListener("timeupdate", onTime);
  }

  function onTime() {
    if (!video || platform.isAd()) return;
    const ms = Math.round(video.currentTime * 1000);
    toPanels({ type: "time", ms, playing: !video.paused });
    if (!video.paused && Date.now() - lastProgressAt >= PROGRESS_EVERY_MS) watchEvent("progress");
  }

  function watchEvent(type, value = null) {
    if (!state?.sessionId || !video || platform.isAd()) return;
    if (type === "play" && !state.transcript && state.wanted) requestTrack(); // players load captions once playing
    if (type === "progress" || type === "play") lastProgressAt = Date.now();
    send({
      type: "watch-event",
      sessionId: state.sessionId,
      event: { type, position_ms: Math.round(video.currentTime * 1000), value, at: new Date().toISOString() },
    });
  }

  // ------------------------------------------------------------ "没听清": replay and remember
  async function missed() {
    if (!video || platform.isAd()) return;
    const position = Math.round(video.currentTime * 1000);
    let replayFrom = Math.max(0, position - NO_TRANSCRIPT_REWIND_MS);
    if (state?.transcript) {
      const r = await send({ type: "missed", platform: platform.name, externalId: state.videoId, positionMs: position });
      if (r.ok) {
        replayFrom = r.data.replay_from_ms;
        toPanels({ type: "missed", issue: r.data });
      }
    }
    video.currentTime = replayFrom / 1000;
    if (video.paused) video.play().catch(() => {});
  }

  const typing = (el) => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  window.addEventListener(
    "keydown",
    (e) => {
      if (e.altKey && !e.ctrlKey && !e.metaKey && e.code === "KeyR" && !typing(e.target)) {
        e.preventDefault();
        e.stopPropagation();
        missed();
      }
    },
    true, // capture: before the site's own shortcuts
  );

  // ------------------------------------------------------------ wiring
  window.addEventListener("message", (event) => {
    if (event.source !== window || event.data?.source !== FROM_PAGE) return;
    const m = event.data;
    if (m.type === "video") onVideo(m.video);
    else if (m.type === "video-left") leave();
    else if (m.type === "track-data") onTrackData(m);
    else if (m.type === "audio-source") onAudioSource(m);
    else if (m.type === "audio-failed") jobFailed(m.videoId, "拿不到这个视频的音频，请刷新页面后重试。");
    else if (m.type === "track-failed" && state?.videoId === m.videoId && !state.transcript) {
      setStatus("fetching"); // retried on the next "play"
    }
  });

  window.addEventListener("pagehide", () => {
    if (state?.sessionId) send({ type: "close-session", sessionId: state.sessionId });
  });

  chrome.runtime.onConnect.addListener((port) => {
    if (port.name !== "sidepanel") return;
    panels.add(port);
    port.onDisconnect.addListener(() => panels.delete(port));
    port.onMessage.addListener((m) => {
      if (m?.type === "seek" && video && Number.isFinite(m.ms)) {
        video.currentTime = Math.max(0, m.ms) / 1000;
      } else if (m?.type === "missed") {
        missed();
      } else if (m?.type === "transcribe") {
        transcribe();
      } else if (m?.type === "cancel-transcription") {
        cancelTranscription();
      }
    });
    port.postMessage(snapshot());
    if (video) port.postMessage({ type: "time", ms: Math.round(video.currentTime * 1000), playing: !video.paused });
  });

  toPage({ type: "hello" }); // the page script may have announced before we were listening
})();
