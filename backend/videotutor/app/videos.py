"""Use cases around watching a video: open it, attach its transcript, follow playback."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from videotutor.domain.transcript import Transcript, normalize
from videotutor.domain.video import CaptionTrack, Video, VideoRef, choose_track
from videotutor.domain.watch import WatchEvent, WatchSession

from .errors import InvalidInputError, NotFoundError
from .ports import (
    CaptionDecoder,
    RecentVideo,
    TranscriptInfo,
    TranscriptRepository,
    VideoRepository,
    WatchRepository,
)

log = logging.getLogger("videos")


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class OpenedVideo:
    video: Video
    session_id: int
    transcript: TranscriptInfo | None
    # The caption track the extension should capture, or None when a good
    # enough transcript is already stored (or no English track exists).
    wanted_track: CaptionTrack | None


class VideoService:
    def __init__(
        self,
        videos: VideoRepository,
        transcripts: TranscriptRepository,
        sessions: WatchRepository,
        decoder: CaptionDecoder,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._videos = videos
        self._transcripts = transcripts
        self._sessions = sessions
        self._decoder = decoder
        self._now = now

    def open_video(
        self,
        ref: VideoRef,
        *,
        title: str,
        url: str,
        duration_ms: int | None,
        tracks: list[CaptionTrack],
    ) -> OpenedVideo:
        now = self._now()
        video = self._videos.upsert(ref, title, url, duration_ms, now)
        session = self._sessions.create(video.id, now)
        stored = self._transcripts.best_info(video.id)
        best = choose_track(tracks)
        wanted = best if best is not None and _improves(best, stored) else None
        return OpenedVideo(video, session.id, stored, wanted)

    def store_captions(
        self, ref: VideoRef, track: CaptionTrack, fmt: str, body: str
    ) -> TranscriptInfo:
        video = self._require_video(ref)
        if fmt not in self._decoder.formats():
            raise InvalidInputError(f"unsupported caption format {fmt!r}")
        try:
            segments = normalize(self._decoder.decode(fmt, body))
            transcript = Transcript(track.language, track.kind, f"{ref.platform}-{fmt}", segments)
        except ValueError as exc:
            raise InvalidInputError(f"captions could not be used: {exc}") from exc
        info = self._transcripts.save(video.id, transcript, self._now())
        log.info(
            "stored %d %s/%s segments for %s",
            info.segment_count,
            track.language,
            track.kind,
            ref.external_id,
        )
        return info

    def transcript(self, ref: VideoRef) -> Transcript:
        video = self._require_video(ref)
        transcript = self._transcripts.best(video.id)
        if transcript is None:
            raise NotFoundError(f"no transcript for {ref.external_id}")
        return transcript

    def record_events(self, session_id: int, events: list[WatchEvent]) -> WatchSession:
        session = self._require_session(session_id)
        applied = [e for e in sorted(events, key=lambda e: e.at) if session.apply(e)]
        self._sessions.save(session, applied)
        return session

    def close_session(self, session_id: int) -> WatchSession:
        session = self._require_session(session_id)
        session.close(self._now())
        self._sessions.save(session, [])
        return session

    def close_unfinished_sessions(self) -> int:
        """At startup: sessions still open belong to a previous run that ended abruptly."""
        return self._sessions.close_unfinished(self._now())

    def recent(self, limit: int = 20) -> list[RecentVideo]:
        return self._videos.recent(max(1, min(limit, 100)))

    def _require_video(self, ref: VideoRef) -> Video:
        video = self._videos.get(ref)
        if video is None:
            raise NotFoundError(f"unknown video {ref.platform}/{ref.external_id}")
        return video

    def _require_session(self, session_id: int) -> WatchSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise NotFoundError(f"unknown watch session {session_id}")
        return session


def _improves(candidate: CaptionTrack, stored: TranscriptInfo | None) -> bool:
    """Is the offered track better than what we already have?"""
    if stored is None:
        return True
    rank = {"manual": 0, "auto": 1, "asr": 2}
    return rank[candidate.kind.value] < rank[stored.kind.value]
