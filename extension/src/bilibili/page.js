// Runs in the Bilibili page's own JavaScript world ("world": "MAIN").
//
// Bilibili only hands out subtitles to a logged-in session (player/v2 answers
// need_login_subtitle: true otherwise -- verified 2026-11), so the requests
// are made from the page itself, with the learner's own cookies. Many study
// videos have subtitles burned into the picture: those have no subtitle file
// at all, and are reported as "no English track".
//
// Same message protocol as youtube/page.js; content.js does not care which
// platform it is talking to. Classic script: MAIN-world scripts cannot be modules.
//
// For videos with no subtitle file it also hands out the audio stream URL
// (lowest bitrate is plenty for speech recognition). The URL is signed for
// this session and expires after a couple of hours, so it is read only when
// the learner asks for recognition.
(() => {
  "use strict";
  if (window.__videotutorPage) return;
  window.__videotutorPage = true;

  const FROM_PAGE = "videotutor-page";
  const FROM_CONTENT = "videotutor-content";
  const post = (msg) => window.postMessage({ source: FROM_PAGE, ...msg }, location.origin);

  const tracksById = new Map(); // track_id -> subtitle_url, for the announced video
  let announcedPage = null; // {bvid, cid, durationMs} of the announced video

  async function getJson(url) {
    const resp = await fetch(url, { credentials: "include" });
    if (!resp.ok) throw new Error(`${url} -> ${resp.status}`);
    return resp.json();
  }

  // "/video/BV1xx411c7mD/?p=2" -> {bvid, part}; part 1 keeps the bare BV id.
  function current() {
    const m = location.pathname.match(/\/video\/(BV[0-9A-Za-z]{10})/);
    if (!m) return null;
    const part = Math.max(1, Number(new URLSearchParams(location.search).get("p")) || 1);
    return { bvid: m[1], part, videoId: part > 1 ? `${m[1]}-p${part}` : m[1] };
  }

  async function describe({ bvid, part, videoId }) {
    const view = await getJson(`https://api.bilibili.com/x/web-interface/view?bvid=${bvid}`);
    const data = view.data;
    if (!data) return null;
    const page = (data.pages || [])[part - 1] || { cid: data.cid, duration: data.duration };
    const player = await getJson(`https://api.bilibili.com/x/player/v2?bvid=${bvid}&cid=${page.cid}`);
    const subs = player.data?.subtitle?.subtitles || [];
    tracksById.clear();
    const tracks = subs
      .filter((s) => s.lan && s.subtitle_url)
      .map((s) => {
        const auto = s.lan.startsWith("ai-");
        const id = String(s.id_str || s.id || s.lan);
        tracksById.set(id, s.subtitle_url.replace(/^\/\//, "https://"));
        return {
          track_id: id.slice(0, 64),
          language: s.lan.replace(/^ai-/, ""), // "ai-en" -> "en", an auto track
          kind: auto ? "auto" : "manual",
          name: s.lan_doc || s.lan,
        };
      });
    const parts = (data.pages || []).length;
    announcedPage = { bvid, cid: page.cid, durationMs: page.duration ? page.duration * 1000 : null };
    return {
      videoId,
      title: parts > 1 && page.part ? `${data.title} · P${part} ${page.part}` : data.title,
      durationMs: page.duration ? page.duration * 1000 : null,
      url: `https://www.bilibili.com/video/${bvid}/${part > 1 ? `?p=${part}` : ""}`,
      tracks,
    };
  }

  let announced = null;
  let pending = false;
  async function announce() {
    const now = current();
    if (!now) {
      if (announced) {
        announced = null;
        post({ type: "video-left" });
      }
      return;
    }
    if (announced === now.videoId || pending) return;
    pending = true;
    try {
      const video = await describe(now);
      if (video && current()?.videoId === now.videoId) {
        announced = now.videoId;
        post({ type: "video", video });
      }
    } catch {
      // Network hiccup or API change: try again on the next tick.
    } finally {
      pending = false;
    }
  }
  setInterval(announce, 1500); // Bilibili switches videos and parts without reloading
  announce();

  async function fetchTrack({ videoId, track }) {
    const url = tracksById.get(track.track_id);
    try {
      if (!url || videoId !== announced) throw new Error("unknown track");
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(String(resp.status));
      post({ type: "track-data", videoId, track, format: "bcc", body: await resp.text() });
    } catch {
      post({ type: "track-failed", videoId, track });
    }
  }

  // Non-WBI playurl answers without login (verified 2026-11); fnval=16 = DASH,
  // where audio is a separate stream.
  async function audioSource({ videoId }) {
    try {
      if (videoId !== announced || !announcedPage) throw new Error("not the current video");
      const { bvid, cid, durationMs } = announcedPage;
      const r = await getJson(`https://api.bilibili.com/x/player/playurl?bvid=${bvid}&cid=${cid}&fnval=16`);
      const audio = (r.data?.dash?.audio || []).slice().sort((a, b) => a.bandwidth - b.bandwidth)[0];
      if (!audio) throw new Error(`no audio stream (code ${r.code})`);
      const urls = [audio.baseUrl || audio.base_url, ...(audio.backupUrl || audio.backup_url || [])].filter(Boolean);
      post({ type: "audio-source", videoId, urls: urls.slice(0, 6), durationMs });
    } catch (err) {
      post({ type: "audio-failed", videoId, error: String(err) });
    }
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.data?.source !== FROM_CONTENT) return;
    if (event.data.type === "fetch-track") fetchTrack(event.data);
    else if (event.data.type === "audio-source") audioSource(event.data);
    else if (event.data.type === "hello") {
      announced = null;
      announce();
    }
  });
})();
