"""Review cards and exercises: spaced retrieval practice for collected words.

Exercise ladder (Nakata's retrieval-format findings, ARCHITECTURE.md 学习方法依据):
recognition is easiest and builds the form-meaning link first; recall and
production come once the word is established.

    RECOGNIZE  pick the right Chinese meaning for the word in its sentence
    SPELL      sentence with a blank + Chinese meaning -> type the word
    RECALL     word in its sentence -> write what it means (AI-graded)
    USE        write your own sentence with the word (AI-graded)

Which rung a card is on follows from how well it is retained (its memory
stability), so a lapse automatically drops it back down.
"""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum

from .vocab import Lexicon, split_clitic, tokenize

GRADUATE_STABILITY_DAYS = 120.0  # remembered for ~4 months: counts as known
BLANK = "_____"


class Grade(StrEnum):
    """Outcome of one attempt, in the scheduler's terms."""

    AGAIN = "again"  # wrong
    HARD = "hard"  # right, with difficulty / partly right
    GOOD = "good"
    EASY = "easy"


class SrsPhase(StrEnum):
    LEARNING = "learning"
    REVIEW = "review"
    RELEARNING = "relearning"


@dataclass(frozen=True)
class SrsState:
    """Scheduler memory for one card. Opaque to everything but the scheduler,
    except `due` (when to show it) and `stability` (how well it is known)."""

    phase: SrsPhase
    due: datetime
    stability: float | None  # days; None before the first review
    difficulty: float | None
    step: int | None
    last_review: datetime | None


@dataclass(frozen=True)
class Card:
    lemma: str
    srs: SrsState
    source_video_id: int | None
    reps: int
    lapses: int
    created_at: datetime

    def is_due(self, now: datetime) -> bool:
        return self.srs.due <= now

    @property
    def graduated(self) -> bool:
        return (self.srs.stability or 0.0) >= GRADUATE_STABILITY_DAYS


class ExerciseKind(IntEnum):
    RECOGNIZE = 0
    SPELL = 1
    RECALL = 2
    USE = 3

    @property
    def ai_graded(self) -> bool:
        return self in (ExerciseKind.RECALL, ExerciseKind.USE)


def exercise_kind_for(card: Card, *, has_distractors: bool, ai_available: bool) -> ExerciseKind:
    """Climb the ladder with memory stability; never pick what cannot be graded."""
    stability = card.srs.stability or 0.0
    if card.reps == 0 or card.srs.phase is SrsPhase.RELEARNING or stability < 2:
        kind = ExerciseKind.RECOGNIZE
    elif stability < 8:
        kind = ExerciseKind.SPELL
    elif stability < 30:
        kind = ExerciseKind.RECALL
    else:
        kind = ExerciseKind.USE
    if kind.ai_graded and not ai_available:
        kind = ExerciseKind.SPELL
    if kind is ExerciseKind.RECOGNIZE and not has_distractors:
        kind = ExerciseKind.SPELL
    return kind


@dataclass(frozen=True)
class Exercise:
    id: str
    lemma: str
    kind: ExerciseKind
    sentence: str  # with BLANK for SPELL; the original line otherwise
    meaning_zh: str
    options: tuple[str, ...]  # RECOGNIZE only
    answer: str  # the correct option, or the word form that fills the blank


def blank_out(sentence: str, lemma: str, lexicon: Lexicon) -> tuple[str, str] | None:
    """Replace the first form of `lemma` in `sentence` with a blank.
    Returns (sentence with blank, the form removed), or None if absent."""
    for t in tokenize(sentence, lexicon):
        if t.lemma == lemma:
            form = sentence[t.start : t.end]
            return sentence[: t.start] + BLANK + sentence[t.end :], form
    return None


def make_options(
    correct: str, pool: Sequence[str], rng: random.Random, n: int = 4
) -> tuple[str, ...]:
    distractors = [p for p in dict.fromkeys(pool) if p and p != correct]
    picked = rng.sample(distractors, k=min(n - 1, len(distractors)))
    options = [correct, *picked]
    rng.shuffle(options)
    return tuple(options)


def grade_choice(exercise: Exercise, chosen: str) -> Grade:
    return Grade.GOOD if chosen == exercise.answer else Grade.AGAIN


_LETTERS = re.compile(r"[^a-z']")


def grade_spelling(exercise: Exercise, typed: str, lexicon: Lexicon) -> Grade:
    """Exact form: GOOD. Another form of the same word ("run" for "running"):
    HARD -- the word was recalled, the grammar was not. One slip: HARD.
    Anything else: AGAIN."""
    guess = _LETTERS.sub("", typed.strip().lower().replace("’", "'"))
    target = exercise.answer.lower().replace("’", "'")
    if not guess:
        return Grade.AGAIN
    if guess == target:
        return Grade.GOOD
    if lexicon.lookup(split_clitic(guess))[0] == exercise.lemma:
        return Grade.HARD
    if _edit_distance(guess, target) == 1 and len(target) >= 5:
        return Grade.HARD
    return Grade.AGAIN


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
