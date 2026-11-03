"""FSRS (py-fsrs) behind the app's SpacedRepetition port.

FSRS models each card's memory with stability (days until recall probability
drops to 90%) and difficulty, and schedules the next review for the desired
retention. Defaults: 90% retention, 1 min / 10 min learning steps.
"""

from __future__ import annotations

from datetime import datetime

import fsrs

from videotutor.domain.review import Grade, SrsPhase, SrsState

_RATING = {
    Grade.AGAIN: fsrs.Rating.Again,
    Grade.HARD: fsrs.Rating.Hard,
    Grade.GOOD: fsrs.Rating.Good,
    Grade.EASY: fsrs.Rating.Easy,
}
_PHASE = {
    fsrs.State.Learning: SrsPhase.LEARNING,
    fsrs.State.Review: SrsPhase.REVIEW,
    fsrs.State.Relearning: SrsPhase.RELEARNING,
}
_STATE = {v: k for k, v in _PHASE.items()}


class FsrsScheduler:
    def __init__(self, desired_retention: float = 0.9, *, fuzz: bool = True) -> None:
        self._scheduler = fsrs.Scheduler(desired_retention=desired_retention, enable_fuzzing=fuzz)

    def new_state(self, now: datetime) -> SrsState:
        return _to_state(fsrs.Card(due=now))

    def review(self, state: SrsState, grade: Grade, now: datetime) -> SrsState:
        card, _ = self._scheduler.review_card(_to_card(state), _RATING[grade], now)
        return _to_state(card)

    def retrievability(self, state: SrsState, now: datetime) -> float:
        return float(self._scheduler.get_card_retrievability(_to_card(state), now))


def _to_card(s: SrsState) -> fsrs.Card:
    return fsrs.Card(
        state=_STATE[s.phase],
        step=s.step,
        stability=s.stability,
        difficulty=s.difficulty,
        due=s.due,
        last_review=s.last_review,
    )


def _to_state(c: fsrs.Card) -> SrsState:
    return SrsState(
        phase=_PHASE[c.state],
        due=c.due,
        stability=c.stability,
        difficulty=c.difficulty,
        step=c.step,
        last_review=c.last_review,
    )
