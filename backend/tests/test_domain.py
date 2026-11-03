from __future__ import annotations

from datetime import UTC, datetime

import pytest

from videotutor.domain.transcript import (
    MAX_SEGMENTS,
    RawCue,
    Segment,
    Transcript,
    normalize,
    segment_index_at,
)
from videotutor.domain.video import CaptionTrack, Platform, TrackKind, VideoRef, choose_track
from videotutor.domain.watch import (
    MAX_CREDIT_GAP_MS,
    WatchEvent,
    WatchEventType,
    WatchSession,
    WatchState,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------- video
def test_video_ref_validates_platform_ids() -> None:
    VideoRef(Platform.YOUTUBE, "iG9CE55wbtY")
    VideoRef(Platform.BILIBILI, "BV1xx411c7mD")
    for bad in ("short", "iG9CE55wbtY/../x", ""):
        with pytest.raises(ValueError, match="not a youtube video id"):
            VideoRef(Platform.YOUTUBE, bad)


@pytest.mark.parametrize(
    ("tracks", "expected"),
    [
        ([("a.en", "en", "auto"), (".en", "en", "manual")], ".en"),
        ([(".en-GB", "en-GB", "manual"), (".en", "en", "manual")], ".en"),
        ([(".en-GB", "en-GB", "manual"), ("a.en", "en", "auto")], ".en-GB"),
        ([("a.en", "en", "auto"), (".fr", "fr", "manual")], "a.en"),
        ([(".zh-CN", "zh-CN", "manual")], None),
        ([], None),
    ],
)
def test_choose_track_prefers_human_english(
    tracks: list[tuple[str, str, str]], expected: str | None
) -> None:
    objs = [CaptionTrack(i, lang, TrackKind(kind)) for i, lang, kind in tracks]
    chosen = choose_track(objs)
    assert (chosen.track_id if chosen else None) == expected


# ---------------------------------------------------------------- transcript
def test_normalize_cleans_merges_and_clips() -> None:
    cues = [
        RawCue(5000, 2000, "second\nline"),
        RawCue(0, 6000, "  first  "),  # overlaps the next one: clipped to 5000
        RawCue(5500, 1000, "second line"),  # rolling repeat of the same text: merged
        RawCue(9000, 1000, "   "),  # empty: dropped
        RawCue(9000, 0, "instant"),  # zero length: kept for 1 ms
    ]
    assert normalize(cues) == (
        Segment(0, 5000, "first"),
        Segment(5000, 7000, "second line"),
        Segment(9000, 9001, "instant"),
    )


def test_segment_lookup_handles_gaps_and_edges() -> None:
    segs = (Segment(0, 1000, "a"), Segment(2000, 3000, "b"))
    assert segment_index_at(segs, 0) == 0
    assert segment_index_at(segs, 999) == 0
    assert segment_index_at(segs, 1500) is None
    assert segment_index_at(segs, 2000) == 1
    assert segment_index_at(segs, 3000) is None
    assert segment_index_at(segs, -5) is None
    assert Transcript("en", TrackKind.MANUAL, "test", segs).index_at(2500) == 1


def test_invalid_segments_and_transcripts_are_rejected() -> None:
    with pytest.raises(ValueError, match="timing"):
        Segment(10, 10, "x")
    with pytest.raises(ValueError, match="empty"):
        Segment(0, 1, "")
    with pytest.raises(ValueError, match="at least one"):
        Transcript("en", TrackKind.MANUAL, "t", ())
    with pytest.raises(ValueError, match="sorted"):
        Transcript("en", TrackKind.MANUAL, "t", (Segment(5, 6, "b"), Segment(0, 1, "a")))
    many = tuple(Segment(i, i + 1, "w") for i in range(MAX_SEGMENTS + 1))
    with pytest.raises(ValueError, match="too many"):
        Transcript("en", TrackKind.MANUAL, "t", many)


# ---------------------------------------------------------------- watch session
def ev(kind: str, pos: int, value: float | None = None) -> WatchEvent:
    return WatchEvent(WatchEventType(kind), pos, T0, value)


def session() -> WatchSession:
    return WatchSession(id=1, video_id=1, state=WatchState.OPEN, started_at=T0)


def test_normal_viewing_credits_played_time() -> None:
    s = session()
    assert s.apply(ev("play", 0))
    assert s.apply(ev("progress", 5000))
    assert s.apply(ev("pause", 8000))
    assert (s.state, s.watched_ms) == (WatchState.PAUSED, 8000)
    assert s.apply(ev("play", 8000))
    assert s.apply(ev("ended", 12000))
    assert (s.state, s.watched_ms, s.max_position_ms) == (WatchState.ENDED, 12000, 12000)


def test_seeking_and_long_gaps_earn_no_credit() -> None:
    s = session()
    s.apply(ev("play", 0))
    s.apply(ev("seek", 60_000))  # skipped a minute
    s.apply(ev("progress", 65_000))
    assert s.watched_ms == 5000
    s.apply(ev("progress", 65_000 + MAX_CREDIT_GAP_MS + 1))  # laptop slept
    assert s.watched_ms == 5000
    s.apply(ev("progress", 65_000 + MAX_CREDIT_GAP_MS + 2001))
    assert s.watched_ms == 7000
    s.apply(ev("seek", 1000))  # going back resets the baseline
    s.apply(ev("progress", 3000))
    assert s.watched_ms == 9000


def test_out_of_order_events_are_ignored() -> None:
    s = session()
    assert not s.apply(ev("pause", 0))  # never started
    assert not s.apply(ev("progress", 1000))
    assert not s.apply(ev("ended", 0))
    s.apply(ev("play", 0))
    assert not s.apply(ev("play", 0))  # duplicate play
    assert s.apply(ev("rate", 0, 1.5))
    s.apply(ev("pause", 2000))
    assert not s.apply(ev("pause", 2000))  # duplicate pause
    assert s.apply(ev("seek", 500))  # seeking while paused is fine
    assert s.last_position_ms is None
    assert s.state is WatchState.PAUSED


def test_close_is_final_and_idempotent() -> None:
    s = session()
    s.apply(ev("play", 0))
    later = datetime(2026, 1, 1, 1, tzinfo=UTC)
    s.close(later)
    s.close(datetime(2027, 1, 1, tzinfo=UTC))
    assert (s.state, s.ended_at) == (WatchState.CLOSED, later)
    assert not s.apply(ev("play", 0))


def test_negative_position_is_invalid() -> None:
    with pytest.raises(ValueError, match="negative"):
        ev("play", -1)
