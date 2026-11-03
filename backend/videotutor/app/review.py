"""Review use cases: cards for collected words, exercise sessions, scheduling.

Two contexts share the same machinery:
* "postwatch": right after a video, practise the words collected from it;
* "review": the daily queue of cards whose scheduled time has come.

Exercises live server-side between "next" and "answer" (bounded, in memory),
so grading never trusts the client about what the right answer was.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from videotutor.domain.review import Card, Exercise, ExerciseKind, Grade
from videotutor.domain.video import VideoRef
from videotutor.domain.vocab import WordStatus

from .errors import InvalidInputError, NotFoundError
from .exercises import ExerciseFactory
from .ports import Attempt, AttemptRepository, CardRepository, SpacedRepetition
from .videos import VideoService, utc_now
from .vocabulary import VocabularyService

MAX_PENDING = 200
MAX_ANSWER_CHARS = 1000


class ReviewContext(StrEnum):
    POSTWATCH = "postwatch"
    REVIEW = "review"


@dataclass(frozen=True)
class ExerciseView:
    """What the learner sees: never contains the answer."""

    id: str
    kind: ExerciseKind
    word: str | None  # hidden (None) when the word itself is the answer
    sentence: str
    hint_zh: str | None
    options: tuple[str, ...]


@dataclass(frozen=True)
class AnswerResult:
    lemma: str  # revealed only now: for spelling, the word is the answer
    grade: Grade
    correct_answer: str
    feedback_zh: str
    next_due: datetime | None
    graduated: bool  # the word is now remembered well enough to count as known


@dataclass(frozen=True)
class ReviewSummary:
    due: int
    total: int
    reviewed_today: int
    waiting_for_gloss: int


@dataclass
class _Pending:
    exercise: Exercise
    context: ReviewContext
    video_id: int | None


class ReviewService:
    def __init__(
        self,
        cards: CardRepository,
        attempts: AttemptRepository,
        srs: SpacedRepetition,
        exercises: ExerciseFactory,
        videos: VideoService,
        vocabulary: VocabularyService,
        prefetch_gloss: Callable[[VideoRef, str], None],
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._cards = cards
        self._attempts = attempts
        self._srs = srs
        self._exercises = exercises
        self._videos = videos
        self._vocabulary = vocabulary
        self._prefetch_gloss = prefetch_gloss
        self._now = now
        self._pending: OrderedDict[str, _Pending] = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ cards
    def set_word_status(
        self, lemma: str, status: WordStatus | None, source: VideoRef | None
    ) -> None:
        """Marking a word: LEARNING creates its review card, anything else retires it."""
        self._vocabulary.set_status(lemma, status)
        if status is not WordStatus.LEARNING:
            self._cards.delete(lemma)
            return
        if self._cards.get(lemma) is not None:
            return
        now = self._now()
        video_id = self._videos.require_video(source).id if source else None
        self._cards.save(Card(lemma, self._srs.new_state(now), video_id, 0, 0, now))
        if source is not None:
            self._prefetch_gloss(source, lemma)  # exercises need the meaning

    def summary(self) -> ReviewSummary:
        now = self._now()
        due, total = self._cards.counts(now)
        # "Today" is the learner's local day; stored times are UTC.
        start = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        waiting = sum(
            1 for c in self._cards.due(now, 500) if not self._exercises.has_material(c.lemma)
        )
        return ReviewSummary(due, total, self._attempts.count_since(start), waiting)

    # ------------------------------------------------------------ sessions
    def next_exercise(
        self, context: ReviewContext, video: VideoRef | None, exclude: set[str]
    ) -> ExerciseView | None:
        video_id: int | None = None
        if context is ReviewContext.POSTWATCH:
            if video is None:
                raise InvalidInputError("a post-watch quiz needs a video")
            video_id = self._videos.require_video(video).id
            candidates = self._cards.for_video(video_id)
        else:
            candidates = self._cards.due(self._now(), 100)
        for card in candidates:
            if card.lemma in exclude:
                continue
            exercise = self._exercises.build(card)
            if exercise is not None:
                with self._lock:
                    self._pending[exercise.id] = _Pending(exercise, context, video_id)
                    while len(self._pending) > MAX_PENDING:
                        self._pending.popitem(last=False)
                return _view(exercise)
        return None

    def answer(self, exercise_id: str, answer: str, duration_ms: int | None) -> AnswerResult:
        """Blocking for AI-graded kinds: run it through the task supervisor."""
        answer = answer.strip()[:MAX_ANSWER_CHARS]
        with self._lock:
            pending = self._pending.pop(exercise_id, None)
        if pending is None:
            raise NotFoundError("this exercise has expired; ask for the next one")
        ex = pending.exercise
        grade, feedback = self._exercises.grade(ex, answer)

        now = self._now()
        result = AnswerResult(ex.lemma, grade, ex.answer, feedback, None, False)
        card = self._cards.get(ex.lemma)  # may have been retired meanwhile
        if card is not None:
            srs = self._srs.review(card.srs, grade, now)
            lapsed = grade is Grade.AGAIN and card.reps > 0
            card = replace(card, srs=srs, reps=card.reps + 1, lapses=card.lapses + int(lapsed))
            if card.graduated:
                self._vocabulary.set_status(card.lemma, WordStatus.KNOWN)
                self._cards.delete(card.lemma)
                result = replace(result, graduated=True)
            else:
                self._cards.save(card)
                result = replace(result, next_due=srs.due)
        self._attempts.add(
            Attempt(
                ex.lemma,
                pending.video_id,
                ex.kind.name.lower(),
                ex.sentence,
                answer,
                grade,
                feedback,
                pending.context.value,
                duration_ms,
                now,
            )
        )
        return result


def _view(ex: Exercise) -> ExerciseView:
    return ExerciseView(
        id=ex.id,
        kind=ex.kind,
        word=None if ex.kind is ExerciseKind.SPELL else ex.lemma,
        sentence=ex.sentence,
        hint_zh=ex.meaning_zh if ex.kind in (ExerciseKind.SPELL, ExerciseKind.USE) else None,
        options=ex.options,
    )
