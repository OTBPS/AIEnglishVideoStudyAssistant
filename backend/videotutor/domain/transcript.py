"""Timed transcripts: what was said, and when."""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .video import TrackKind

_SPACE = re.compile(r"\s+")
MAX_SEGMENTS = 20_000  # ~10 hours of speech; anything larger is not a transcript


@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int
    text: str

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError(f"bad segment timing {self.start_ms}..{self.end_ms}")
        if not self.text:
            raise ValueError("empty segment text")


@dataclass(frozen=True)
class Transcript:
    language: str
    kind: TrackKind
    source: str  # where it came from, e.g. "youtube-json3"
    segments: tuple[Segment, ...]

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("a transcript needs at least one segment")
        if len(self.segments) > MAX_SEGMENTS:
            raise ValueError(f"too many segments ({len(self.segments)})")
        starts = [s.start_ms for s in self.segments]
        if starts != sorted(starts):
            raise ValueError("segments must be sorted by start time")

    def index_at(self, t_ms: int) -> int | None:
        return segment_index_at(self.segments, t_ms)


@dataclass(frozen=True)
class RawCue:
    """A cue as a decoder found it: untrusted, maybe overlapping or empty."""

    start_ms: int
    duration_ms: int
    text: str


def clean_text(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def normalize(cues: Iterable[RawCue]) -> tuple[Segment, ...]:
    """Turn decoder output into a clean, non-overlapping timeline.

    * whitespace (including caption line breaks) collapses to single spaces;
    * empty cues are dropped;
    * a cue that repeats the previous text while overlapping it is merged
      (rolling auto-captions repeat lines);
    * each segment ends no later than the next one starts, so "the line at
      time t" is always unambiguous.
    """
    items = sorted(
        ((c.start_ms, c.start_ms + max(c.duration_ms, 0), clean_text(c.text)) for c in cues),
        key=lambda x: x[0],
    )
    merged: list[tuple[int, int, str]] = []
    for start, end, text in items:
        if not text or start < 0:
            continue
        if merged and merged[-1][2] == text and start <= merged[-1][1]:
            prev_start, prev_end, prev_text = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end), prev_text)
            continue
        merged.append((start, end, text))

    out: list[Segment] = []
    for i, (start, raw_end, text) in enumerate(merged):
        nxt = merged[i + 1][0] if i + 1 < len(merged) else None
        end = raw_end if nxt is None else min(raw_end, nxt)
        if end <= start:
            end = start + 1  # zero-length cue: keep the words, give them a moment
            if nxt is not None and end > nxt:
                continue  # two cues at the same instant: the later one wins
        out.append(Segment(start, end, text))
    return tuple(out)


def segment_index_at(segments: Sequence[Segment], t_ms: int) -> int | None:
    """Index of the segment playing at t_ms, or None in a gap / outside."""
    starts = [s.start_ms for s in segments]
    i = bisect.bisect_right(starts, t_ms) - 1
    if i >= 0 and t_ms < segments[i].end_ms:
        return i
    return None
