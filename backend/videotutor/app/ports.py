"""Interfaces to the outside world. The app layer depends on these; adapters
in videotutor.infra implement them; bootstrap wires one to the other.

Add a port only when there is a real second implementation or a real need
to fake it in tests -- not speculatively.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from videotutor.domain.transcript import RawCue, Transcript
from videotutor.domain.video import TrackKind, Video, VideoRef
from videotutor.domain.watch import WatchEvent, WatchSession


@dataclass(frozen=True)
class Locations:
    """Every path the app uses. Built once by infra.paths, then passed around.

    install_dir is replaced on every upgrade; home survives upgrades and
    uninstalls (unless the user opts in to deleting it).
    """

    install_dir: Path
    home: Path
    data_dir: Path
    logs_dir: Path
    models_dir: Path
    extension_dir: Path
    web_dir: Path
    icon_file: Path
    settings_file: Path
    runtime_file: Path
    database_file: Path


class Desktop(Protocol):
    """Shell integration: what a user would do with Explorer or a browser."""

    def open_path(self, path: Path) -> None: ...

    def open_url(self, url: str) -> None: ...

    def message(self, text: str, title: str, *, error: bool = False) -> None: ...


# ---------------------------------------------------------------- persistence
@dataclass(frozen=True)
class TranscriptInfo:
    language: str
    kind: TrackKind
    source: str
    segment_count: int


@dataclass(frozen=True)
class RecentVideo:
    video: Video
    last_opened_at: datetime
    watched_ms: int
    max_position_ms: int
    transcript: TranscriptInfo | None


class VideoRepository(Protocol):
    def upsert(
        self, ref: VideoRef, title: str, url: str, duration_ms: int | None, now: datetime
    ) -> Video: ...

    def get(self, ref: VideoRef) -> Video | None: ...

    def recent(self, limit: int) -> list[RecentVideo]: ...


class TranscriptRepository(Protocol):
    def save(self, video_id: int, transcript: Transcript, now: datetime) -> TranscriptInfo:
        """Insert, or replace the transcript with the same language and kind."""
        ...

    def best(self, video_id: int) -> Transcript | None:
        """The transcript a learner should see: human-made first, then plain English."""
        ...

    def best_info(self, video_id: int) -> TranscriptInfo | None: ...


class WatchRepository(Protocol):
    def create(self, video_id: int, now: datetime) -> WatchSession: ...

    def get(self, session_id: int) -> WatchSession | None: ...

    def save(self, session: WatchSession, events: list[WatchEvent]) -> None:
        """Persist the session's new state together with the events that produced it."""
        ...

    def close_unfinished(self, now: datetime) -> int:
        """Close every session left open (e.g. by a crash). Returns how many."""
        ...


# ---------------------------------------------------------------- captions
class CaptionDecoder(Protocol):
    """Turns a platform's caption payload into raw cues."""

    def formats(self) -> frozenset[str]: ...

    def decode(self, fmt: str, body: str) -> list[RawCue]: ...
