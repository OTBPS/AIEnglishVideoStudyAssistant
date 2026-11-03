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


# ---------------------------------------------------------------- vocabulary (0.3.0)
LEMMA_PATTERN = r"^[a-z]+$"


class WordOut(BaseModel):
    lemma: str
    count: int
    rank: int | None
    status: str | None
    known: bool


class VocabularyOut(BaseModel):
    level: int
    token_count: int
    known_count: int
    coverage: float
    difficulty: str
    words: list[WordOut]
    # Per transcript segment: [start, end, index into `words`], or index -1 for a name.
    # The side panel highlights by these spans, so tokenising lives only in Python.
    spans: list[list[list[int]]]


class WordStatusIn(BaseModel):
    status: Literal["known", "learning", "ignored"] | None
    # The video the word was collected from: a new review card links to it.
    platform: Platform | None = None
    external_id: str | None = Field(default=None, max_length=32)


class GlossOut(BaseModel):
    lemma: str
    part_of_speech: str
    definition_en: str
    definition_zh: str
    example: str


class GlossIn(BaseModel):
    lemma: str = Field(pattern=LEMMA_PATTERN, max_length=40)


class PreviewWordOut(BaseModel):
    word: WordOut
    example: str
    gloss: GlossOut | None


class QuestionOut(BaseModel):
    question: str
    hint_zh: str


class GuideOut(BaseModel):
    prediction_prompt: str
    questions: list[QuestionOut]
    based_on_partial_transcript: bool


class PrewatchOut(BaseModel):
    status: str
    error: str | None
    words: list[PreviewWordOut]
    guide: GuideOut | None


class PrewatchStartIn(BaseModel):
    auto: bool = False


# ---------------------------------------------------------------- settings (0.3.0)
class SettingsOut(BaseModel):
    vocabulary_level: int
    auto_prewatch: bool


class SettingsIn(BaseModel):
    vocabulary_level: int | None = Field(default=None, ge=500, le=40000)
    auto_prewatch: bool | None = None


class LlmConfigOut(BaseModel):
    provider: str
    model: str
    base_url: str
    has_key: bool


class LlmConfigIn(BaseModel):
    provider: Literal["claude", "openai_compatible"]
    model: str = Field(min_length=1, max_length=100)
    base_url: str = Field(default="", max_length=500)
    api_key: str | None = Field(default=None, max_length=500)


class LlmTestOut(BaseModel):
    ok: bool
    error: str | None
    config: LlmConfigOut


# ---------------------------------------------------------------- review (0.4.0)
class ReviewSummaryOut(BaseModel):
    due: int
    total: int
    reviewed_today: int
    waiting_for_gloss: int


class NextExerciseIn(BaseModel):
    context: Literal["postwatch", "review"]
    platform: Platform | None = None
    external_id: str | None = Field(default=None, max_length=32)
    exclude: list[str] = Field(default_factory=list, max_length=500)


class ExerciseOut(BaseModel):
    id: str
    kind: Literal["recognize", "spell", "recall", "use"]
    word: str | None
    sentence: str
    hint_zh: str | None
    options: list[str]


class NextExerciseOut(BaseModel):
    exercise: ExerciseOut | None


class AnswerIn(BaseModel):
    exercise_id: str = Field(min_length=1, max_length=64)
    answer: str = Field(max_length=1000)
    duration_ms: int | None = Field(default=None, ge=0, le=3_600_000)


class AnswerOut(BaseModel):
    lemma: str
    grade: Literal["again", "hard", "good", "easy"]
    correct_answer: str
    feedback_zh: str
    next_due: datetime | None
    graduated: bool


class ComprehensionIn(BaseModel):
    answer: str = Field(max_length=1000)


class ComprehensionOut(BaseModel):
    grade: Literal["again", "hard", "good", "easy"]
    feedback_zh: str
    reference_answer: str


# ---------------------------------------------------------------- listening (0.5.0)
class MissedIn(BaseModel):
    position_ms: int = Field(ge=0, le=48 * 3600 * 1000)


class ListeningIssueOut(BaseModel):
    start_ms: int
    end_ms: int
    text: str
    count: int
    replay_from_ms: int
