// Runs in the YouTube page's own JavaScript world ("world": "MAIN", document_start).
//
// Why here and not in the isolated content script: caption URLs need a
// proof-of-origin token only the player can mint, so re-requesting them
// returns an empty body (verified 2026-11). We therefore listen to the
// player's *own* timedtext responses, and read the player's video details.
//
// Talks to content.js only through window.postMessage. Classic script (no
// imports): MAIN-world content scripts cannot be ES modules.
(() => {
  "use strict";
  if (window.__videotutorPage) return;
  window.__videotutorPage = true;

  const FROM_PAGE = "videotutor-page";
  const FROM_CONTENT = "videotutor-content";
  const MAX_CAPTURED = 20;
  const TRIGGER_WAIT_MS = 15000;
  const AD_WAIT_MS = 180000;

  const captured = new Map(); // "videoId|lang|kind" -> {format, body}
  const inflight = new Set();

  const post = (msg) => window.postMessage({ source: FROM_PAGE, ...msg }, location.origin);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const keyOf = (videoId, language, kind) => `${videoId}|${language}|${kind}`;

  // ------------------------------------------------------------ capture
  function remember(url, body) {
    if (!body) return;
    let u;
    try {
      u = new URL(url, location.href);
    } catch {
      return;
    }
    const videoId = u.searchParams.get("v");
    const language = u.searchParams.get("lang");
    if (!videoId || !language || u.searchParams.has("tlang")) return; // skip machine translations
    const kind = u.searchParams.get("kind") === "asr" ? "auto" : "manual";
    const format = u.searchParams.get("fmt") || "srv3";
    captured.set(keyOf(videoId, language, kind), { format, body });
    while (captured.size > MAX_CAPTURED) captured.delete(captured.keys().next().value);
  }

  const isTimedtext = (url) => typeof url === "string" && url.includes("/api/timedtext");

  const xhrOpen = XMLHttpRequest.prototype.open;
  const xhrSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__videotutorUrl = String(url);
    return xhrOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function (...args) {
    if (isTimedtext(this.__videotutorUrl)) {
      this.addEventListener("load", () => {
        if (this.status === 200 && (this.responseType === "" || this.responseType === "text")) {
          remember(this.__videotutorUrl, this.responseText);
        }
      });
    }
    return xhrSend.apply(this, args);
  };

  const pageFetch = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input?.url;
    const result = pageFetch.call(this, input, init);
    if (isTimedtext(url)) {
      result
        .then((resp) => (resp.ok ? resp.clone().text() : ""))
        .then((text) => remember(url, text))
        .catch(() => {});
    }
    return result; // the page gets exactly what it would have got
  };

  // ------------------------------------------------------------ video details
  const player = () => document.getElementById("movie_player");

  function currentVideo() {
    const response = player()?.getPlayerResponse?.();
    const details = response?.videoDetails;
    if (!details?.videoId) return null;
    const urlId = new URLSearchParams(location.search).get("v");
    if (urlId && urlId !== details.videoId) return null; // stale during navigation
    const tracks = (response.captions?.playerCaptionsTracklistRenderer?.captionTracks || [])
      .filter((t) => t.vssId && t.languageCode)
      .map((t) => ({
        track_id: t.vssId,
        language: t.languageCode,
        kind: t.kind === "asr" ? "auto" : "manual",
        name: t.name?.simpleText ?? (t.name?.runs || []).map((r) => r.text).join(""),
      }));
    const seconds = Number(details.lengthSeconds || 0);
    return {
      videoId: details.videoId,
      title: details.title || document.title,
      durationMs: seconds > 0 ? seconds * 1000 : null,
      url: `https://www.youtube.com/watch?v=${details.videoId}`,
      tracks,
    };
  }

  let announced = null;
  function announce() {
    if (location.pathname !== "/watch") {
      if (announced) {
        announced = null;
        post({ type: "video-left" });
      }
      return;
    }
    const video = currentVideo();
    if (!video || announced === video.videoId) return;
    announced = video.videoId;
    post({ type: "video", video });
  }

  document.addEventListener("yt-navigate-finish", () => setTimeout(announce, 0));
  document.addEventListener("yt-player-updated", announce);
  setInterval(announce, 1500); // safety net for missed navigation events

  // ------------------------------------------------------------ fetching a track
  async function triggerTrack(videoId, track) {
    const p = player();
    if (!p?.setOption) return;
    const key = keyOf(videoId, track.language, track.kind);
    // While an ad plays, the captions module describes the *ad* (verified
    // 2026-11: its tracklist had only the ad's auto tracks). Wait it out.
    const adDeadline = Date.now() + AD_WAIT_MS;
    while (p.classList.contains("ad-showing") && Date.now() < adDeadline) await sleep(500);
    if (captured.has(key)) return; // the player loaded it on its own meanwhile
    let previous = null;
    try {
      previous = p.getOption("captions", "track");
    } catch {}
    const hadCaptions = Boolean(previous?.languageCode);
    let target = { languageCode: track.language };
    try {
      const list = p.getOption("captions", "tracklist", { includeAsr: true }) || [];
      const listed = list.find((t) => t.vss_id === track.track_id);
      if (listed) target = listed;
      else if (track.kind === "auto") target = { languageCode: track.language, kind: "asr" };
    } catch {}
    try {
      p.loadModule?.("captions");
      p.setOption("captions", "track", target);
    } catch {}
    const deadline = Date.now() + TRIGGER_WAIT_MS;
    while (!captured.has(key) && Date.now() < deadline) await sleep(250);
    // Put the learner's own caption choice back the way it was.
    try {
      if (hadCaptions) p.setOption("captions", "track", previous);
      else {
        p.setOption("captions", "track", {});
        p.unloadModule?.("captions");
      }
    } catch {}
  }

  async function fetchTrack({ videoId, track }) {
    const key = keyOf(videoId, track.language, track.kind);
    if (inflight.has(key)) return;
    inflight.add(key);
    try {
      if (!captured.has(key)) await triggerTrack(videoId, track);
      const hit = captured.get(key);
      if (hit) post({ type: "track-data", videoId, track, format: hit.format, body: hit.body });
      else post({ type: "track-failed", videoId, track });
    } finally {
      inflight.delete(key);
    }
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.data?.source !== FROM_CONTENT) return;
    if (event.data.type === "fetch-track") fetchTrack(event.data);
    else if (event.data.type === "hello") {
      announced = null; // content script (re)started: announce again
      announce();
    }
  });
})();
