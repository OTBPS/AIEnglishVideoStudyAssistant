"""Turning a card into an exercise, and grading the learner's answer to it."""

from __future__ import annotations

import random
import uuid

from videotutor.domain.review import (
    Card,
    Exercise,
    ExerciseKind,
    Grade,
    blank_out,
    exercise_kind_for,
    grade_choice,
    grade_spelling,
    make_options,
)
from videotutor.domain.vocab import Lexicon

from .grading import ai_grade
from .ports import GlossLookup, LanguageModelProvider

DISTRACTOR_POOL = 30


class ExerciseFactory:
    def __init__(
        self,
        glosses: GlossLookup,
        lexicon: Lexicon,
        llm: LanguageModelProvider,
        rng: random.Random | None = None,
    ) -> None:
        self._glosses = glosses
        self._lexicon = lexicon
        self._llm = llm
        self._rng = rng or random.Random()  # noqa: S311 -- shuffling options, not security

    def has_material(self, lemma: str) -> bool:
        """Exercises need the word's meaning (fetched from the AI when collected)."""
        return self._glosses.latest_gloss(lemma) is not None

    def build(self, card: Card) -> Exercise | None:
        gloss = self._glosses.latest_gloss(card.lemma)
        if gloss is None:
            return None
        ai = self._llm.current() is not None
        pool = self._glosses.meaning_pool(card.lemma, DISTRACTOR_POOL)
        kind = exercise_kind_for(card, has_distractors=len(pool) >= 3, ai_available=ai)
        ex_id = uuid.uuid4().hex
        if kind is ExerciseKind.SPELL:
            blanked = blank_out(gloss.example, card.lemma, self._lexicon)
            if blanked is not None:
                return Exercise(
                    ex_id, card.lemma, kind, blanked[0], gloss.definition_zh, (), blanked[1]
                )
            # The example does not contain the word in a blankable form.
            if len(pool) >= 3:
                kind = ExerciseKind.RECOGNIZE
            elif ai:
                kind = ExerciseKind.RECALL
            else:
                return None
        options = (
            make_options(gloss.definition_zh, pool, self._rng)
            if kind is ExerciseKind.RECOGNIZE
            else ()
        )
        return Exercise(
            ex_id,
            card.lemma,
            kind,
            gloss.example,
            gloss.definition_zh,
            options,
            gloss.definition_zh,
        )

    def grade(self, ex: Exercise, answer: str) -> tuple[Grade, str]:
        """(grade, feedback). Blocking for AI-graded kinds."""
        if ex.kind is ExerciseKind.RECOGNIZE:
            return grade_choice(ex, answer), ""
        if ex.kind is ExerciseKind.SPELL:
            grade = grade_spelling(ex, answer, self._lexicon)
            return grade, (f"意思对了，这里应该写成 {ex.answer}。" if grade is Grade.HARD else "")
        if not answer:
            return Grade.AGAIN, "没有作答，先看看正确答案吧。"
        if ex.kind is ExerciseKind.RECALL:
            task = (
                f'The learner saw the word "{ex.lemma}" in this sentence:\n"{ex.sentence}"\n'
                f"and wrote what they think it means (Chinese or English):\n{answer}\n\n"
                f"The intended meaning is: {ex.answer}. Grade whether they understood this "
                "sense of the word. better_answer: a concise correct meaning in Chinese."
            )
        else:
            task = (
                f'The learner was asked to write their own English sentence using "{ex.lemma}" '
                f"(meaning: {ex.answer}). They wrote:\n{answer}\n\n"
                "Grade whether the word is used correctly and naturally in that sense; small "
                "grammar slips elsewhere are only 'partly'. better_answer: a corrected or "
                "improved version of their sentence."
            )
        v = ai_grade(self._llm, task)
        feedback = f"{v.feedback_zh}\n参考：{v.better_answer}" if v.better_answer else v.feedback_zh
        return v.grade, feedback
