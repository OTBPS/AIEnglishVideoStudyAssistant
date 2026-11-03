"""Videos, transcripts and watch sessions -- what the extension talks to while you watch."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query

from videotutor.app.ports import TranscriptInfo
from videotutor.app.videos import VideoService
from videotutor.domain.video import CaptionTrack, Platform, TrackKind
from videotutor.domain.watch import WatchEvent, WatchSession

from ..schemas import (
    CaptionsIn,
    EventsIn,
    OpenVideoIn,
    OpenVideoOut,
    RecentVideoOut,
    SegmentOut,
    SessionOut,
    TrackIn,
    TranscriptInfoOut,
    TranscriptOut,
)
from .common import video_ref


def _track(t: TrackIn) -> CaptionTrack:
    return CaptionTrack(t.track_id, t.language, TrackKind(t.kind), t.name)


def _info(i: TranscriptInfo) -> TranscriptInfoOut:
    return TranscriptInfoOut(
        language=i.language, kind=i.kind.value, source=i.source, segment_count=i.segment_count
    )


def _session(s: WatchSession) -> SessionOut:
    return SessionOut(
        id=s.id, state=s.state.value, watched_ms=s.watched_ms, max_position_ms=s.max_position_ms
    )


def router(videos: VideoService) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.post("/videos/open")
    def open_video(body: OpenVideoIn) -> OpenVideoOut:
        opened = videos.open_video(
            video_ref(body.platform, body.external_id),
            title=body.title,
            url=body.url,
            duration_ms=body.duration_ms,
            tracks=[_track(t) for t in body.tracks],
        )
        wanted = opened.wanted_track
        return OpenVideoOut(
            video_id=opened.video.id,
            session_id=opened.session_id,
            transcript=None if opened.transcript is None else _info(opened.transcript),
            wanted_track=None
            if wanted is None
            else TrackIn(
                track_id=wanted.track_id,
                language=wanted.language,
                kind="manual" if wanted.kind is TrackKind.MANUAL else "auto",
                name=wanted.name,
            ),
        )

    @r.put("/videos/{platform}/{external_id}/captions")
    def store_captions(platform: Platform, external_id: str, body: CaptionsIn) -> TranscriptInfoOut:
        info = videos.store_captions(
            video_ref(platform, external_id), _track(body.track), body.format, body.body
        )
        return _info(info)

    @r.get("/videos/{platform}/{external_id}/transcript")
    def transcript(platform: Platform, external_id: str) -> TranscriptOut:
        t = videos.transcript(video_ref(platform, external_id))
        return TranscriptOut(
            language=t.language,
            kind=t.kind.value,
            source=t.source,
            segments=[
                SegmentOut(start_ms=s.start_ms, end_ms=s.end_ms, text=s.text) for s in t.segments
            ],
        )

    @r.get("/videos/recent")
    def recent(limit: int = Query(default=20, ge=1, le=100)) -> list[RecentVideoOut]:
        return [
            RecentVideoOut(
                platform=v.video.ref.platform.value,
                external_id=v.video.ref.external_id,
                title=v.video.title,
                url=v.video.url,
                duration_ms=v.video.duration_ms,
                last_opened_at=v.last_opened_at,
                watched_ms=v.watched_ms,
                max_position_ms=v.max_position_ms,
                has_transcript=v.transcript is not None,
            )
            for v in videos.recent(limit)
        ]

    @r.post("/sessions/{session_id}/events")
    def events(session_id: int, body: EventsIn) -> SessionOut:
        now = datetime.now(UTC)
        domain_events = [
            WatchEvent(e.type, e.position_ms, e.at or now, e.value) for e in body.events
        ]
        return _session(videos.record_events(session_id, domain_events))

    @r.post("/sessions/{session_id}/close")
    def close(session_id: int) -> SessionOut:
        return _session(videos.close_session(session_id))

    return r
