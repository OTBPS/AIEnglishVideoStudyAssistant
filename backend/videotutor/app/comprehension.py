"""Post-watch: grade the learner's answers to the questions set before watching."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from videotutor.domain.review import Grade
from videotutor.domain.video import VideoRef

from .errors import NotFoundError
from .grading import ai_grade
from .ports import Attempt, AttemptRepository, LanguageModelProvider, PrewatchRepository
from .videos import VideoService, utc_now

MAX_ANSWER_CHARS = 1000
MAX_TRANSCRIPT_CHARS = 60_000


@dataclass(frozen=True)
class ComprehensionResult:
    grade: Grade
    feedback_zh: str
    reference_answer: str


class ComprehensionService:
    def __init__(
        self,
        videos: VideoService,
        guides: PrewatchRepository,
        attempts: AttemptRepository,
        llm: LanguageModelProvider,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._videos = videos
        self._guides = guides
        self._attempts = attempts
        self._llm = llm
        self._now = now

    def grade(self, video: VideoRef, index: int, answer: str) -> ComprehensionResult:
        """Blocking: run it through the task supervisor."""
        record = self._videos.require_video(video)
        guide = self._guides.guide(record.id)
        if guide is None or not 0 <= index < len(guide.questions):
            raise NotFoundError("no such question for this video")
        question = guide.questions[index].question
        answer = answer.strip()[:MAX_ANSWER_CHARS]
        transcript = "\n".join(s.text for s in self._videos.transcript(video).segments)
        v = ai_grade(
            self._llm,
            (
                f"Video transcript:\n{transcript[:MAX_TRANSCRIPT_CHARS]}\n\n"
                f"Question: {question}\nLearner's answer: {answer or '(no answer)'}\n\n"
                "Grade the answer against what the video actually says. better_answer: a model "
                "answer in one or two simple English sentences."
            ),
        )
        grade = v.grade if answer else Grade.AGAIN
        self._attempts.add(
            Attempt(
                None,
                record.id,
                "comprehension",
                question,
                answer,
                grade,
                v.feedback_zh,
                "postwatch",
                None,
                self._now(),
            )
        )
        return ComprehensionResult(grade, v.feedback_zh, v.better_answer)
