"""AI grading of free-text answers: one verdict scale shared by every task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from videotutor.domain.review import Grade

from .errors import LlmNotConfiguredError, LlmOutputError
from .ports import LanguageModelProvider

GRADER_SYSTEM = (
    "You grade short answers from a Chinese-speaking learner of English. Be encouraging but "
    "honest. Judge meaning, not spelling or grammar slips, unless the task is about them. "
    "Feedback is in Simplified Chinese, one or two short sentences."
)
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partly", "wrong"]},
        "feedback_zh": {"type": "string"},
        "better_answer": {"type": "string"},
    },
    "required": ["verdict", "feedback_zh", "better_answer"],
    "additionalProperties": False,
}
_GRADE = {"correct": Grade.GOOD, "partly": Grade.HARD, "wrong": Grade.AGAIN}


class _Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: str
    feedback_zh: str
    better_answer: str


@dataclass(frozen=True)
class Verdict:
    grade: Grade
    feedback_zh: str
    better_answer: str


def ai_grade(llm: LanguageModelProvider, task: str) -> Verdict:
    """Blocking: run it through the task supervisor."""
    model = llm.current()
    if model is None:
        raise LlmNotConfiguredError("no API key configured")
    data = model.generate_json(
        system=GRADER_SYSTEM, prompt=task, schema=VERDICT_SCHEMA, max_tokens=4000
    )
    try:
        v = _Verdict.model_validate(data)
    except ValidationError as exc:
        raise LlmOutputError(f"unusable grading answer: {exc}") from exc
    if v.verdict not in _GRADE:
        raise LlmOutputError(f"unknown verdict {v.verdict!r}")
    return Verdict(_GRADE[v.verdict], v.feedback_zh.strip(), v.better_answer.strip())
