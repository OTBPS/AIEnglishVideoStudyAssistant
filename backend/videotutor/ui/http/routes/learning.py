"""Vocabulary, word marks and pre-watch material for a video."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Path

from videotutor.app.prewatch import PrewatchService, PrewatchState
from videotutor.app.vocabulary import VocabularyService
from videotutor.core.tasks import TaskSupervisor
from videotutor.domain.prewatch import Gloss
from videotutor.domain.video import Platform
from videotutor.domain.vocab import WordStat, WordStatus

from ..schemas import (
    LEMMA_PATTERN,
    GlossIn,
    GlossOut,
    GuideOut,
    PreviewWordOut,
    PrewatchOut,
    PrewatchStartIn,
    QuestionOut,
    VocabularyOut,
    WordOut,
    WordStatusIn,
)
from .common import video_ref

GLOSS_TIMEOUT_S = 90.0


def _word(w: WordStat) -> WordOut:
    return WordOut(
        lemma=w.lemma,
        count=w.count,
        rank=w.rank,
        status=w.status.value if w.status else None,
        known=w.known,
    )


def _gloss(g: Gloss) -> GlossOut:
    return GlossOut(
        lemma=g.lemma,
        part_of_speech=g.part_of_speech,
        definition_en=g.definition_en,
        definition_zh=g.definition_zh,
        example=g.example,
    )


def _prewatch(s: PrewatchState) -> PrewatchOut:
    return PrewatchOut(
        status=s.status.value,
        error=s.error,
        words=[
            PreviewWordOut(
                word=_word(p.word), example=p.example, gloss=_gloss(p.gloss) if p.gloss else None
            )
            for p in s.words
        ],
        guide=None
        if s.guide is None
        else GuideOut(
            prediction_prompt=s.guide.prediction_prompt,
            questions=[
                QuestionOut(question=q.question, hint_zh=q.hint_zh) for q in s.guide.questions
            ],
            based_on_partial_transcript=s.guide.based_on_partial_transcript,
        ),
    )


def router(
    vocabulary: VocabularyService, prewatch: PrewatchService, tasks: TaskSupervisor
) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/videos/{platform}/{external_id}/vocabulary")
    def video_vocabulary(platform: Platform, external_id: str) -> VocabularyOut:
        v = vocabulary.for_video(video_ref(platform, external_id))
        index = {w.lemma: i for i, w in enumerate(v.profile.words)}
        return VocabularyOut(
            level=v.level,
            token_count=v.profile.token_count,
            known_count=v.profile.known_count,
            coverage=round(v.profile.coverage, 4),
            difficulty=v.profile.difficulty.value,
            words=[_word(w) for w in v.profile.words],
            spans=[
                [[t.start, t.end, -1 if t.lemma in v.names else index[t.lemma]] for t in toks]
                for toks in v.tokens
            ],
        )

    @r.put("/words/{lemma}")
    def set_word_status(
        lemma: Annotated[str, Path(pattern=LEMMA_PATTERN, max_length=40)], body: WordStatusIn
    ) -> dict[str, str | None]:
        vocabulary.set_status(lemma, WordStatus(body.status) if body.status else None)
        return {"lemma": lemma, "status": body.status}

    @r.get("/videos/{platform}/{external_id}/prewatch")
    def prewatch_state(platform: Platform, external_id: str) -> PrewatchOut:
        return _prewatch(prewatch.state(video_ref(platform, external_id)))

    @r.post("/videos/{platform}/{external_id}/prewatch")
    def prewatch_start(platform: Platform, external_id: str, body: PrewatchStartIn) -> PrewatchOut:
        return _prewatch(prewatch.start(video_ref(platform, external_id), auto=body.auto))

    @r.post("/videos/{platform}/{external_id}/glosses")
    async def gloss(platform: Platform, external_id: str, body: GlossIn) -> GlossOut:
        ref = video_ref(platform, external_id)
        handle = tasks.run_blocking(
            f"gloss:{body.lemma}",
            lambda: prewatch.gloss_word(ref, body.lemma),
            timeout=GLOSS_TIMEOUT_S,
        )
        return _gloss(await asyncio.wrap_future(handle.future))

    return r
