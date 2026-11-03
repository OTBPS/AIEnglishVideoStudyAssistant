"""Review queue, post-watch quiz, and comprehension answers."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from videotutor.app.comprehension import ComprehensionService
from videotutor.app.review import ReviewContext, ReviewService
from videotutor.core.tasks import TaskSupervisor
from videotutor.domain.video import Platform

from ..schemas import (
    AnswerIn,
    AnswerOut,
    ComprehensionIn,
    ComprehensionOut,
    ExerciseOut,
    NextExerciseIn,
    NextExerciseOut,
    ReviewSummaryOut,
)
from .common import video_ref

GRADE_TIMEOUT_S = 90.0


def router(
    review: ReviewService, comprehension_service: ComprehensionService, tasks: TaskSupervisor
) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/review/summary")
    def summary() -> ReviewSummaryOut:
        s = review.summary()
        return ReviewSummaryOut(
            due=s.due,
            total=s.total,
            reviewed_today=s.reviewed_today,
            waiting_for_gloss=s.waiting_for_gloss,
        )

    @r.post("/review/next")
    def next_exercise(body: NextExerciseIn) -> NextExerciseOut:
        video = None
        if body.platform is not None and body.external_id is not None:
            video = video_ref(body.platform, body.external_id)
        elif body.context == "postwatch":
            raise HTTPException(status_code=422, detail="a post-watch quiz needs a video")
        ex = review.next_exercise(ReviewContext(body.context), video, set(body.exclude))
        if ex is None:
            return NextExerciseOut(exercise=None)
        return NextExerciseOut(
            exercise=ExerciseOut(
                id=ex.id,
                kind=ex.kind.name.lower(),  # type: ignore[arg-type]
                word=ex.word,
                sentence=ex.sentence,
                hint_zh=ex.hint_zh,
                options=list(ex.options),
            )
        )

    @r.post("/review/answer")
    async def answer(body: AnswerIn) -> AnswerOut:
        handle = tasks.run_blocking(
            "grade-answer",
            lambda: review.answer(body.exercise_id, body.answer, body.duration_ms),
            timeout=GRADE_TIMEOUT_S,
        )
        res = await asyncio.wrap_future(handle.future)
        return AnswerOut(
            lemma=res.lemma,
            grade=res.grade.value,
            correct_answer=res.correct_answer,
            feedback_zh=res.feedback_zh,
            next_due=res.next_due,
            graduated=res.graduated,
        )

    @r.post("/videos/{platform}/{external_id}/comprehension/{index}")
    async def comprehension(
        platform: Platform, external_id: str, index: int, body: ComprehensionIn
    ) -> ComprehensionOut:
        ref = video_ref(platform, external_id)
        handle = tasks.run_blocking(
            "grade-comprehension",
            lambda: comprehension_service.grade(ref, index, body.answer),
            timeout=GRADE_TIMEOUT_S,
        )
        res = await asyncio.wrap_future(handle.future)
        return ComprehensionOut(
            grade=res.grade.value,
            feedback_zh=res.feedback_zh,
            reference_answer=res.reference_answer,
        )

    return r
