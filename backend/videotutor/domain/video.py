"""Videos and the caption tracks a platform offers for them."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Platform(StrEnum):
    YOUTUBE = "youtube"
    BILIBILI = "bilibili"


_ID_PATTERNS = {
    Platform.YOUTUBE: re.compile(r"^[A-Za-z0-9_-]{11}$"),
    # Multi-part videos: each part has its own transcript. Part 1 is the bare
    # BV id (unchanged from 0.2), later parts are "BV...-p2" and so on.
    Platform.BILIBILI: re.compile(r"^BV[0-9A-Za-z]{10}(-p[2-9]|-p[1-9][0-9]{1,2})?$"),
}


@dataclass(frozen=True)
class VideoRef:
    """A video as the platform identifies it."""

    platform: Platform
    external_id: str

    def __post_init__(self) -> None:
        if not _ID_PATTERNS[self.platform].match(self.external_id):
            raise ValueError(f"not a {self.platform} video id: {self.external_id!r}")


@dataclass(frozen=True)
class Video:
    id: int
    ref: VideoRef
    title: str
    url: str
    duration_ms: int | None


class TrackKind(StrEnum):
    MANUAL = "manual"  # uploaded by a person
    AUTO = "auto"  # the platform's speech recognition
    ASR = "asr"  # our own local transcription (M4)


@dataclass(frozen=True)
class CaptionTrack:
    track_id: str  # opaque, platform-specific (YouTube: vssId such as ".en" / "a.en")
    language: str  # BCP 47, e.g. "en", "en-GB"
    kind: TrackKind
    name: str = ""

    @property
    def is_english(self) -> bool:
        return self.language.lower() == "en" or self.language.lower().startswith("en-")


def choose_track(tracks: list[CaptionTrack]) -> CaptionTrack | None:
    """The English track a learner should see: human-made beats machine-made,
    plain "en" beats regional variants. None if there is no English at all."""

    def rank(t: CaptionTrack) -> tuple[int, int]:
        human = 0 if t.kind is TrackKind.MANUAL else 1
        plain = 0 if t.language.lower() == "en" else 1
        return human, plain

    english = [t for t in tracks if t.is_english]
    return min(english, key=rank) if english else None
