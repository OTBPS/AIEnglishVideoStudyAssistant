"""Wire format of the HTTP API (pydantic), separate from the domain dataclasses.

Changing a field here changes what the extension sees: adding optional fields
is compatible; renaming or removing one needs a protocol bump (ARCHITECTURE.md).
Size limits live here so oversized input is rejected before any work is done.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from videotutor.domain.video import Platform
from videotutor.domain.watch import WatchEventType

LANG_PATTERN = r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$"
MAX_CAPTION_CHARS = 8_000_000


class TrackIn(BaseModel):
    track_id: str = Field(min_length=1, max_length=64)
    language: str = Field(pattern=LANG_PATTERN, max_length=32)
    kind: Literal["manual", "auto"]
    name: str = Field(default="", max_length=200)


class OpenVideoIn(BaseModel):
    platform: Platform
    external_id: str = Field(min_length=1, max_length=32)
    title: str = Field(default="", max_length=500)
    url: str = Field(default="", max_length=2000)
    duration_ms: int | None = Field(default=None, ge=0)
    tracks: list[TrackIn] = Field(default_factory=list, max_length=300)


class TranscriptInfoOut(BaseModel):
    language: str
    kind: str
    source: str
    segment_count: int


class OpenVideoOut(BaseModel):
    video_id: int
    session_id: int
    transcript: TranscriptInfoOut | None
    wanted_track: TrackIn | None


class CaptionsIn(BaseModel):
    track: TrackIn
    format: str = Field(min_length=1, max_length=16)
    body: str = Field(min_length=1, max_length=MAX_CAPTION_CHARS)


class SegmentOut(BaseModel):
    start_ms: int
    end_ms: int
    text: str


class TranscriptOut(BaseModel):
    language: str
    kind: str
    source: str
    segments: list[SegmentOut]


class WatchEventIn(BaseModel):
    type: WatchEventType
    position_ms: int = Field(ge=0, le=48 * 3600 * 1000)
    value: float | None = Field(default=None, ge=0, le=16)
    at: datetime | None = None  # client clock; the server's is used when absent


class EventsIn(BaseModel):
    events: list[WatchEventIn] = Field(min_length=1, max_length=500)


class SessionOut(BaseModel):
    id: int
    state: str
    watched_ms: int
    max_position_ms: int


class RecentVideoOut(BaseModel):
    platform: str
    external_id: str
    title: str
    url: str
    duration_ms: int | None
    last_opened_at: datetime
    watched_ms: int
    max_position_ms: int
    has_transcript: bool
