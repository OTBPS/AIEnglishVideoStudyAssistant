"""Review cards, exercises, grading and scheduling (domain + FSRS + service)."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from videotutor.app.comprehension import ComprehensionService
from videotutor.app.errors import LlmNotConfiguredError, NotFoundError
from videotutor.app.exercises import ExerciseFactory
from videotutor.app.review import ReviewContext, ReviewService
from videotutor.domain.prewatch import Gloss, PrewatchGuide, Question
from videotutor.domain.review import (
    BLANK,
    Card,
    Exercise,
    ExerciseKind,
    Grade,
    SrsPhase,
    SrsState,
    blank_out,
    exercise_kind_for,
    grade_choice,
    grade_spelling,
    make_options,
)
from videotutor.domain.vocab import WordStatus
from videotutor.infra.db.repositories import (
    SqliteAttemptRepository,
    SqliteCardRepository,
    SqlitePrewatchRepository,
)
from videotutor.infra.srs import FsrsScheduler

from .conftest import FakeLanguageModel
from .test_prewatch import REF, Env
from .test_vocab import LEXICON

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def promote(c: Card, stability: float, *, due: datetime = T0, reps: int | None = None) -> Card:
    """A card as it would be after several successful reviews."""
    srs = SrsState(SrsPhase.REVIEW, due, stability, 5.0, None, due - timedelta(days=1))
    return replace(c, srs=srs, reps=c.reps if reps is None else reps)


def card(stability: float | None = None, reps: int = 0, phase: SrsPhase = SrsPhase.REVIEW) -> Card:
    srs = SrsState(phase, T0, stability, None, None, None)
    return Card("durable", srs, None, reps, 0, T0)


# ---------------------------------------------------------------- domain
@pytest.mark.parametrize(
    ("c", "expected"),
    [
        (card(None, 0, SrsPhase.LEARNING), ExerciseKind.RECOGNIZE),
        (card(5.0, 3, SrsPhase.RELEARNING), ExerciseKind.RECOGNIZE),
        (card(1.0, 1), ExerciseKind.RECOGNIZE),
        (card(4.0, 2), ExerciseKind.SPELL),
        (card(15.0, 4), ExerciseKind.RECALL),
        (card(60.0, 6), ExerciseKind.USE),
    ],
)
def test_ladder_follows_stability(c: Card, expected: ExerciseKind) -> None:
    assert exercise_kind_for(c, has_distractors=True, ai_available=True) is expected


def test_ladder_never_picks_what_cannot_be_graded() -> None:
    assert (
        exercise_kind_for(card(60.0, 6), has_distractors=True, ai_available=False)
        is ExerciseKind.SPELL
    )
    assert (
        exercise_kind_for(card(None, 0), has_distractors=False, ai_available=True)
        is ExerciseKind.SPELL
    )


def test_graduation_threshold() -> None:
    assert not card(119.0).graduated
    assert card(120.0).graduated


def test_blank_out_finds_inflected_forms() -> None:
    assert blank_out("Memories become remarkably durable.", "durable", LEXICON) == (
        f"Memories become remarkably {BLANK}.",
        "durable",
    )
    assert blank_out("We revisited it twice.", "revisit", LEXICON) == (
        f"We {BLANK} it twice.",
        "revisited",
    )
    assert blank_out("Nothing here.", "durable", LEXICON) is None


def test_options_include_the_answer_once_and_distinct_distractors() -> None:
    opts = make_options("持久的", ["持久的", "重温", "加强", "加强", "非常"], random.Random(1))
    assert sorted(opts) == sorted(["持久的", "重温", "加强", "非常"])
    assert make_options("a", [], random.Random(1)) == ("a",)


def test_grading_choice_and_spelling() -> None:
    mcq = Exercise("1", "durable", ExerciseKind.RECOGNIZE, "s", "持久的", ("持久的", "x"), "持久的")
    assert grade_choice(mcq, "持久的") is Grade.GOOD and grade_choice(mcq, "x") is Grade.AGAIN
    spell = Exercise("2", "revisit", ExerciseKind.SPELL, "s", "重温", (), "revisited")
    assert grade_spelling(spell, " Revisited ", LEXICON) is Grade.GOOD
    assert grade_spelling(spell, "revisit", LEXICON) is Grade.HARD  # right word, wrong form
    assert grade_spelling(spell, "revisitd", LEXICON) is Grade.HARD  # one slip
    assert grade_spelling(spell, "revsiited", LEXICON) is Grade.HARD  # adjacent letters swapped
    assert grade_spelling(spell, "review", LEXICON) is Grade.AGAIN
    assert grade_spelling(spell, "  ", LEXICON) is Grade.AGAIN


# ---------------------------------------------------------------- FSRS adapter
def test_fsrs_schedules_further_out_after_success() -> None:
    srs = FsrsScheduler(fuzz=False)
    s0 = srs.new_state(T0)
    assert s0.phase is SrsPhase.LEARNING and s0.due == T0
    good = srs.review(s0, Grade.GOOD, T0)
    again = srs.review(s0, Grade.AGAIN, T0)
    assert good.due > again.due > T0
    assert good.stability is not None and good.last_review == T0
    later = srs.review(good, Grade.EASY, good.due)
    assert later.phase is SrsPhase.REVIEW and later.due - good.due > timedelta(days=1)
    assert 0 < srs.retrievability(later, later.due + timedelta(days=30)) < 1


# ---------------------------------------------------------------- service
def verdict(v: str, fb: str = "很好", better: str = "") -> dict[str, Any]:
    return {"verdict": v, "feedback_zh": fb, "better_answer": better}


class ReviewEnv(Env):
    def __init__(self, tmp: Any, model: FakeLanguageModel | None) -> None:
        super().__init__(tmp, model)
        self.clock = T0
        self.cards = SqliteCardRepository(self.db)
        self.repo = SqlitePrewatchRepository(self.db)
        attempts = SqliteAttemptRepository(self.db)
        self.review = ReviewService(
            cards=self.cards,
            attempts=attempts,
            srs=FsrsScheduler(fuzz=False),
            exercises=ExerciseFactory(self.repo, LEXICON, self.provider, random.Random(7)),
            videos=self.videos,
            vocabulary=self.vocabulary,
            prefetch_gloss=self.prewatch.prefetch_gloss,
            now=lambda: self.clock,
        )
        self.comprehension = ComprehensionService(
            self.videos, self.repo, attempts, self.provider, now=lambda: self.clock
        )
        video_id = self.videos.require_video(REF).id
        glosses = [
            Gloss(
                "creativity",
                "n.",
                "making new things",
                "创造力",
                "Creativity is as important as literacy.",
            ),
            Gloss(
                "literacy",
                "n.",
                "being able to read",
                "读写能力",
                "Creativity is as important as literacy.",
            ),
            Gloss("hierarchy", "n.", "an order of rank", "等级制度", "the hierarchy of subjects"),
            Gloss(
                "frighten",
                "v.",
                "to make afraid",
                "使害怕",
                "they are not frightened of being wrong.",
            ),
        ]
        self.repo.save_glosses(video_id, glosses, "fake", T0)


@pytest.fixture
def make_review(tmp_path: Any) -> Any:
    envs: list[ReviewEnv] = []

    def make(model: FakeLanguageModel | None = None) -> ReviewEnv:
        env = ReviewEnv(tmp_path / str(len(envs)), model)
        envs.append(env)
        return env

    yield make
    for env in envs:
        env.close()


def test_collecting_a_word_creates_and_retires_a_card(make_review: Any) -> None:
    env = make_review()
    env.review.set_word_status("creativity", WordStatus.LEARNING, REF)
    c = env.cards.get("creativity")
    assert c is not None and c.source_video_id == env.videos.require_video(REF).id
    env.review.set_word_status("creativity", WordStatus.LEARNING, REF)  # idempotent
    assert env.review.summary().total == 1
    env.review.set_word_status("creativity", WordStatus.KNOWN, None)
    assert env.cards.get("creativity") is None


def test_postwatch_recognition_round(make_review: Any) -> None:
    env = make_review()
    for w in ("creativity", "frighten"):
        env.review.set_word_status(w, WordStatus.LEARNING, REF)
    ex = env.review.next_exercise(ReviewContext.POSTWATCH, REF, set())
    assert ex is not None and ex.kind is ExerciseKind.RECOGNIZE and ex.word == "creativity"
    assert "创造力" in ex.options and ex.hint_zh is None  # the meaning is not given away

    wrong = env.review.answer(ex.id, next(o for o in ex.options if o != "创造力"), 3000)
    assert wrong.grade is Grade.AGAIN and wrong.correct_answer == "创造力"
    assert wrong.next_due is not None and wrong.next_due < T0 + timedelta(hours=1)
    with pytest.raises(NotFoundError):
        env.review.answer(ex.id, "创造力", None)  # an exercise is answered once

    second = env.review.next_exercise(ReviewContext.POSTWATCH, REF, {"creativity"})
    assert second is not None and second.word == "frighten"
    assert (
        env.review.next_exercise(ReviewContext.POSTWATCH, REF, {"creativity", "frighten"}) is None
    )
    assert env.review.summary().reviewed_today == 1


def test_daily_queue_only_offers_due_cards_and_spelling_hides_the_word(make_review: Any) -> None:
    env = make_review()
    env.review.set_word_status("frighten", WordStatus.LEARNING, REF)
    ex = env.review.next_exercise(ReviewContext.REVIEW, None, set())
    assert ex is not None
    res = env.review.answer(ex.id, "使害怕", 1000)
    assert res.grade is Grade.GOOD and res.next_due is not None
    assert env.review.next_exercise(ReviewContext.REVIEW, None, set()) is None  # not due yet

    env.clock = res.next_due + timedelta(minutes=1)
    c = env.cards.get("frighten")
    assert c is not None
    env.cards.save(promote(c, 4.0, due=env.clock, reps=2))
    spell = env.review.next_exercise(ReviewContext.REVIEW, None, set())
    assert spell is not None and spell.kind is ExerciseKind.SPELL
    assert spell.word is None and BLANK in spell.sentence and spell.hint_zh == "使害怕"
    assert env.review.answer(spell.id, "frightened", None).grade is Grade.GOOD


def test_ai_graded_kinds_and_graduation(make_review: Any) -> None:
    model = FakeLanguageModel(verdict("partly", "接近了", "创造力"), verdict("correct"))
    env = make_review(model)
    env.review.set_word_status("creativity", WordStatus.LEARNING, REF)
    c = env.cards.get("creativity")
    assert c is not None
    env.cards.save(promote(c, 15.0, reps=5))
    recall = env.review.next_exercise(ReviewContext.REVIEW, None, set())
    assert recall is not None and recall.kind is ExerciseKind.RECALL
    res = env.review.answer(recall.id, "创新？", 5000)
    assert res.grade is Grade.HARD and "接近了" in res.feedback_zh and "创造力" in res.feedback_zh
    assert "creativity" in str(model.calls[0]["prompt"])

    c = env.cards.get("creativity")
    assert c is not None
    env.cards.save(promote(c, 200.0))
    use = env.review.next_exercise(ReviewContext.REVIEW, None, set())
    assert use is not None and use.kind is ExerciseKind.USE and use.hint_zh == "创造力"
    done = env.review.answer(use.id, "Creativity matters in school.", None)
    assert done.graduated and env.cards.get("creativity") is None
    assert env.vocabulary.for_video(REF).profile.words  # still analysable
    statuses = {w.lemma: w.status for w in env.vocabulary.for_video(REF).profile.words}
    assert statuses["creativity"] is WordStatus.KNOWN


def test_empty_ai_answer_needs_no_call(make_review: Any) -> None:
    model = FakeLanguageModel()
    env = make_review(model)
    env.review.set_word_status("creativity", WordStatus.LEARNING, REF)
    c = env.cards.get("creativity")
    assert c is not None
    env.cards.save(promote(c, 15.0, reps=3))
    ex = env.review.next_exercise(ReviewContext.REVIEW, None, set())
    assert ex is not None
    assert env.review.answer(ex.id, "", None).grade is Grade.AGAIN
    assert model.calls == []


def test_words_without_meaning_wait_and_get_one_fetched(make_review: Any) -> None:
    model = FakeLanguageModel(
        {
            "glosses": [
                {
                    "lemma": "chance",
                    "part_of_speech": "n.",
                    "definition_en": "a possibility",
                    "definition_zh": "机会",
                }
            ]
        }
    )
    env = make_review(model)
    env.review.set_word_status(
        "chance", WordStatus.LEARNING, REF
    )  # no gloss yet: fetched in background
    for _ in range(100):
        if env.repo.latest_gloss("chance") is not None:
            break
        import time

        time.sleep(0.02)
    gloss = env.repo.latest_gloss("chance")
    assert gloss is not None and gloss.definition_zh == "机会"


def test_comprehension_grading(make_review: Any) -> None:
    model = FakeLanguageModel(
        verdict("correct", "答对了", "Schools educate people out of creativity.")
    )
    env = make_review(model)
    video_id = env.videos.require_video(REF).id
    env.repo.save_guide(
        video_id,
        PrewatchGuide("猜猜看", (Question("What do schools do to creativity?", "注意 educating"),)),
        "fake",
        T0,
    )
    res = env.comprehension.grade(REF, 0, "They kill it.")
    assert res.grade is Grade.GOOD and res.reference_answer.startswith("Schools")
    assert "Creativity is as important as literacy." in str(model.calls[0]["prompt"])
    with pytest.raises(NotFoundError):
        env.comprehension.grade(REF, 5, "x")
    env.provider.model = None
    with pytest.raises(LlmNotConfiguredError):
        env.comprehension.grade(REF, 0, "x")
