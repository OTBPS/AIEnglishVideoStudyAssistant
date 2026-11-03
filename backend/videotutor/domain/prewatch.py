"""Pre-watch material: word glosses and the questions a learner watches for."""

from __future__ import annotations

import re
from dataclasses import dataclass

LEMMA_RE = re.compile(r"^[a-z]+$")


@dataclass(frozen=True)
class Gloss:
    lemma: str
    part_of_speech: str
    definition_en: str  # learner-dictionary style, simpler than the word itself
    definition_zh: str
    example: str  # a sentence from the video where the word is used

    def __post_init__(self) -> None:
        if not LEMMA_RE.match(self.lemma):
            raise ValueError(f"not a lemma: {self.lemma!r}")
        if not self.definition_en.strip() or not self.definition_zh.strip():
            raise ValueError(f"empty definition for {self.lemma!r}")


@dataclass(frozen=True)
class Question:
    question: str  # English: comprehension is practised in the target language
    hint_zh: str  # what to listen for, in Chinese, for lower levels


@dataclass(frozen=True)
class PrewatchGuide:
    """Prediction first, then questions to watch for (Vandergrift & Goh's
    planning stage; previewing questions helps more than previewing words)."""

    prediction_prompt: str  # Chinese: "看标题，你觉得他会讲什么？"
    questions: tuple[Question, ...]
    based_on_partial_transcript: bool = False

    def __post_init__(self) -> None:
        if not self.prediction_prompt.strip():
            raise ValueError("empty prediction prompt")
        if not 1 <= len(self.questions) <= 5:
            raise ValueError(f"expected 1-5 questions, got {len(self.questions)}")
