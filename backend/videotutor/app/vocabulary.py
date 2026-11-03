"""Use cases around words: how hard a video is for this learner, and marking words."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from itertools import chain

from videotutor.domain.prewatch import LEMMA_RE
from videotutor.domain.transcript import Transcript
from videotutor.domain.video import VideoRef
from videotutor.domain.vocab import (
    Lexicon,
    Token,
    VocabularyProfile,
    WordStatus,
    build_profile,
    detect_names,
    ends_sentence,
    tokenize,
)

from .errors import InvalidInputError
from .ports import WordStatusRepository
from .settings import SettingsStore
from .videos import VideoService, utc_now


@dataclass(frozen=True)
class VideoVocabulary:
    transcript: Transcript
    tokens: tuple[tuple[Token, ...], ...]  # per transcript segment
    profile: VocabularyProfile
    level: int
    names: frozenset[str]

    def examples(self, lemma: str, limit: int = 2) -> list[str]:
        """Transcript lines that use the word, for glossing in context."""
        out: list[str] = []
        for seg, toks in zip(self.transcript.segments, self.tokens, strict=True):
            if any(t.lemma == lemma for t in toks):
                out.append(seg.text)
                if len(out) == limit:
                    break
        return out


class VocabularyService:
    def __init__(
        self,
        lexicon: Lexicon,
        videos: VideoService,
        statuses: WordStatusRepository,
        settings: SettingsStore,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._lexicon = lexicon
        self._videos = videos
        self._statuses = statuses
        self._settings = settings
        self._now = now

    def for_video(self, ref: VideoRef) -> VideoVocabulary:
        transcript = self._videos.transcript(ref)
        tokens: list[tuple[Token, ...]] = []
        starts = True
        for seg in transcript.segments:
            tokens.append(tuple(tokenize(seg.text, self._lexicon, starts_sentence=starts)))
            starts = ends_sentence(seg.text)
        level = self._settings.current().vocabulary_level
        names = detect_names(chain.from_iterable(tokens))
        profile = build_profile(chain.from_iterable(tokens), self._statuses.all(), level, names)
        return VideoVocabulary(transcript, tuple(tokens), profile, level, names)

    def set_status(self, lemma: str, status: WordStatus | None) -> None:
        if not LEMMA_RE.match(lemma) or len(lemma) > 40:
            raise InvalidInputError(f"not a word: {lemma!r}")
        self._statuses.set(lemma, status, self._now())
