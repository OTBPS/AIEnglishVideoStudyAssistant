"""VideoService against the real SQLite repositories."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from videotutor.app.errors import InvalidInputError, NotFoundError
from videotutor.app.videos import VideoService
from videotutor.domain.video import CaptionTrack, Platform, TrackKind, VideoRef
from videotutor.domain.watch import WatchEvent, WatchEventType, WatchState
from videotutor.infra.captions.youtube import YouTubeCaptionDecoder
from videotutor.infra.db.database import Database
from videotutor.infra.db.repositories import (
    SqliteTranscriptRepository,
    SqliteVideoRepository,
    SqliteWatchRepository,
)

REF = VideoRef(Platform.YOUTUBE, "iG9CE55wbtY")
MANUAL = CaptionTrack(".en", "en", TrackKind.MANUAL, "English")
AUTO = CaptionTrack("a.en", "en", TrackKind.AUTO, "English (auto)")


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


def json3(*lines: tuple[int, int, str]) -> str:
    return json.dumps(
        {"events": [{"tStartMs": s, "dDurationMs": d, "segs": [{"utf8": t}]} for s, d, t in lines]}
    )


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database, _ = Database.open(tmp_path / "tutor.db")
    yield database
    database.close()


def make_service(db: Database) -> VideoService:
    return VideoService(
        SqliteVideoRepository(db),
        SqliteTranscriptRepository(db),
        SqliteWatchRepository(db),
        YouTubeCaptionDecoder(),
        now=Clock(),
    )


@pytest.fixture
def svc(db: Database) -> VideoService:
    return make_service(db)


def open_(svc: VideoService, tracks: list[CaptionTrack], title: str = "Talk") -> int:
    return svc.open_video(
        REF, title=title, url="https://youtu.be/x", duration_ms=60_000, tracks=tracks
    ).session_id


def test_open_asks_for_the_best_track_until_it_is_stored(svc: VideoService) -> None:
    first = svc.open_video(REF, title="Talk", url="u", duration_ms=1, tracks=[AUTO, MANUAL])
    assert first.wanted_track == MANUAL and first.transcript is None

    svc.store_captions(REF, AUTO, "json3", json3((0, 1000, "auto line")))
    again = svc.open_video(REF, title="", url="", duration_ms=None, tracks=[AUTO, MANUAL])
    assert again.wanted_track == MANUAL, "a human transcript would be an improvement"
    assert again.transcript is not None and again.transcript.kind is TrackKind.AUTO
    assert again.video.title == "Talk", "an empty title never overwrites a known one"
    assert again.session_id != first.session_id

    svc.store_captions(REF, MANUAL, "json3", json3((0, 1000, "human line")))
    third = svc.open_video(REF, title="Talk", url="u", duration_ms=1, tracks=[AUTO, MANUAL])
    assert third.wanted_track is None
    assert svc.transcript(REF).segments[0].text == "human line"


def test_storing_again_replaces_the_same_track(svc: VideoService) -> None:
    open_(svc, [MANUAL])
    svc.store_captions(REF, MANUAL, "json3", json3((0, 1000, "v1")))
    info = svc.store_captions(REF, MANUAL, "json3", json3((0, 1000, "v2"), (1000, 500, "more")))
    assert info.segment_count == 2
    assert [s.text for s in svc.transcript(REF).segments] == ["v2", "more"]


def test_unusable_captions_are_rejected(svc: VideoService) -> None:
    open_(svc, [MANUAL])
    with pytest.raises(InvalidInputError, match="unsupported"):
        svc.store_captions(REF, MANUAL, "vtt", "WEBVTT")
    with pytest.raises(InvalidInputError, match="could not be used"):
        svc.store_captions(REF, MANUAL, "json3", json3((0, 1000, "   ")))
    with pytest.raises(InvalidInputError, match="could not be used"):
        svc.store_captions(REF, MANUAL, "json3", "garbage")


def test_unknown_things_are_not_found(svc: VideoService) -> None:
    with pytest.raises(NotFoundError):
        svc.transcript(REF)
    with pytest.raises(NotFoundError):
        svc.store_captions(REF, MANUAL, "json3", json3((0, 1, "x")))
    with pytest.raises(NotFoundError):
        svc.record_events(999, [])
    open_(svc, [])
    with pytest.raises(NotFoundError, match="no transcript"):
        svc.transcript(REF)


def test_watch_session_is_persisted(svc: VideoService, db: Database) -> None:
    sid = open_(svc, [MANUAL])
    t = datetime(2026, 11, 3, tzinfo=UTC)
    events = [
        WatchEvent(WatchEventType.PAUSE, 9000, t + timedelta(seconds=9)),
        WatchEvent(WatchEventType.PLAY, 0, t),  # arrives late: sorted by time
        WatchEvent(WatchEventType.PROGRESS, 5000, t + timedelta(seconds=5)),
    ]
    s = svc.record_events(sid, events)
    assert (s.state, s.watched_ms) == (WatchState.PAUSED, 9000)

    fresh = make_service(db)  # nothing cached in memory
    s2 = fresh.record_events(sid, [WatchEvent(WatchEventType.PLAY, 9000, t)])
    assert (s2.state, s2.watched_ms) == (WatchState.PLAYING, 9000)
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM watch_events").fetchone()[0] == 4


def test_close_and_startup_cleanup(svc: VideoService) -> None:
    a = open_(svc, [])
    b = open_(svc, [])
    assert svc.close_session(a).state is WatchState.CLOSED
    assert svc.close_unfinished_sessions() == 1  # only b was still open
    assert svc.close_session(b).state is WatchState.CLOSED


def test_recent_videos_summarise_progress(svc: VideoService) -> None:
    sid = open_(svc, [MANUAL], title="First")
    t = datetime(2026, 11, 3, tzinfo=UTC)
    svc.record_events(
        sid, [WatchEvent(WatchEventType.PLAY, 0, t), WatchEvent(WatchEventType.PAUSE, 4000, t)]
    )
    svc.store_captions(REF, MANUAL, "json3", json3((0, 1000, "x")))
    other = VideoRef(Platform.YOUTUBE, "aaaaaaaaaaa")
    svc.open_video(other, title="Second", url="", duration_ms=None, tracks=[])

    recent = svc.recent()
    assert [r.video.title for r in recent] == ["Second", "First"]
    assert recent[1].watched_ms == 4000 and recent[1].transcript is not None
    assert recent[0].transcript is None
