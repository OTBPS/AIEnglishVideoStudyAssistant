"""Interfaces to the outside world. The app layer depends on these; adapters
in videotutor.infra implement them; bootstrap wires one to the other.

Add a port only when there is a real second implementation or a real need
to fake it in tests -- not speculatively.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from videotutor.domain.listening import ListeningIssue
from videotutor.domain.prewatch import Gloss, PrewatchGuide
from videotutor.domain.review import Card, Grade, SrsState
from videotutor.domain.transcript import RawCue, Transcript
from videotutor.domain.video import TrackKind, Video, VideoRef
from videotutor.domain.vocab import WordStatus
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
    lexicon_file: Path


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


# ---------------------------------------------------------------- vocabulary
class WordStatusRepository(Protocol):
    def all(self) -> dict[str, WordStatus]: ...

    def set(self, lemma: str, status: WordStatus | None, now: datetime) -> None:
        """None removes the learner's mark (the frequency rule applies again)."""
        ...


class PrewatchRepository(Protocol):
    def glosses(self, video_id: int) -> dict[str, Gloss]: ...

    def latest_gloss(self, lemma: str) -> Gloss | None: ...

    def save_glosses(
        self, video_id: int, glosses: list[Gloss], model: str, now: datetime
    ) -> None: ...

    def guide(self, video_id: int) -> PrewatchGuide | None: ...

    def save_guide(
        self, video_id: int, guide: PrewatchGuide, model: str, now: datetime
    ) -> None: ...


# ---------------------------------------------------------------- language model & secrets
class LanguageModel(Protocol):
    """One structured request. Implementations raise app.errors.LlmError subclasses."""

    @property
    def model_name(self) -> str: ...

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]: ...


class LanguageModelProvider(Protocol):
    def current(self) -> LanguageModel | None:
        """The model the settings describe, or None when no key is configured."""
        ...


class SecretStore(Protocol):
    """Where API keys live: never in settings.json, never in logs."""

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


# ---------------------------------------------------------------- review (M3)
class SpacedRepetition(Protocol):
    """The scheduling algorithm (FSRS today), kept replaceable."""

    def new_state(self, now: datetime) -> SrsState: ...

    def review(self, state: SrsState, grade: Grade, now: datetime) -> SrsState: ...

    def retrievability(self, state: SrsState, now: datetime) -> float: ...


@dataclass(frozen=True)
class Attempt:
    lemma: str | None
    video_id: int | None
    kind: str  # an ExerciseKind name, or "comprehension"
    prompt: str
    answer: str
    grade: Grade
    feedback: str
    context: str  # "postwatch" | "review"
    duration_ms: int | None
    at: datetime


class CardRepository(Protocol):
    def get(self, lemma: str) -> Card | None: ...

    def save(self, card: Card) -> None: ...

    def delete(self, lemma: str) -> None: ...

    def due(self, now: datetime, limit: int) -> list[Card]: ...

    def for_video(self, video_id: int) -> list[Card]: ...

    def counts(self, now: datetime) -> tuple[int, int]:
        """(cards due now, all cards)."""
        ...


class AttemptRepository(Protocol):
    def add(self, attempt: Attempt) -> None: ...

    def count_since(self, since: datetime) -> int: ...


class GlossLookup(Protocol):
    """Glosses across all videos, for building exercises."""

    def latest_gloss(self, lemma: str) -> Gloss | None: ...

    def meaning_pool(self, exclude: str, limit: int) -> list[str]:
        """Chinese meanings of other words, for multiple-choice distractors."""
        ...


# ---------------------------------------------------------------- listening (M4)
class ListeningIssueRepository(Protocol):
    def record(
        self, video_id: int, start_ms: int, end_ms: int, text: str, now: datetime
    ) -> ListeningIssue:
        """Insert the line, or count one more replay of it."""
        ...

    def for_video(self, video_id: int) -> list[ListeningIssue]: ...
