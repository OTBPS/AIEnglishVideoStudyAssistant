"""Pre-watch use cases: gloss the few key words, set questions to watch for.

One language-model call per video produces both the glosses and the guide
(cheaper and faster than two). It runs as a supervised background task; the
result is stored, so reopening the video costs nothing. Example sentences come
from the transcript, never from the model, so they cannot be invented.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from videotutor.core.tasks import TaskHandle, TaskState, TaskSupervisor
from videotutor.domain.prewatch import Gloss, PrewatchGuide, Question
from videotutor.domain.video import VideoRef
from videotutor.domain.vocab import WordStat, preview_candidates

from .errors import LlmError, LlmNotConfiguredError, LlmOutputError
from .ports import LanguageModel, LanguageModelProvider, PrewatchRepository
from .settings import SettingsStore
from .videos import VideoService, utc_now
from .vocabulary import VideoVocabulary, VocabularyService

log = logging.getLogger("prewatch")

PREVIEW_WORDS = 5
MAX_TRANSCRIPT_WORDS = 20_000  # ~2.5 hours of speech; longer videos use the first part
GENERATE_TIMEOUT_S = 180.0

SYSTEM = (
    "You help Chinese-speaking learners of English get ready to watch an English video. "
    "Write definitions the way a good learner's dictionary does: plain words, shorter and "
    "simpler than the word being defined, and matching the sense used in the video. "
    "Chinese text must be Simplified Chinese."
)


# ---------------------------------------------------------------- output contract
class _GlossOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lemma: str
    part_of_speech: str
    definition_en: str
    definition_zh: str


class _QuestionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    hint_zh: str


class _GlossesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    glosses: list[_GlossOut]


class _PrewatchOut(_GlossesOut):
    prediction_prompt: str
    questions: list[_QuestionOut]


_GLOSS_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lemma": {"type": "string"},
        "part_of_speech": {"type": "string"},
        "definition_en": {"type": "string"},
        "definition_zh": {"type": "string"},
    },
    "required": ["lemma", "part_of_speech", "definition_en", "definition_zh"],
    "additionalProperties": False,
}
GLOSSES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"glosses": {"type": "array", "items": _GLOSS_ITEM}},
    "required": ["glosses"],
    "additionalProperties": False,
}
PREWATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "glosses": {"type": "array", "items": _GLOSS_ITEM},
        "prediction_prompt": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"question": {"type": "string"}, "hint_zh": {"type": "string"}},
                "required": ["question", "hint_zh"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["glosses", "prediction_prompt", "questions"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- state model
class PrewatchStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"  # no AI key yet
    IDLE = "idle"  # nothing generated, nothing running
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True)
class PreviewWord:
    word: WordStat
    example: str
    gloss: Gloss | None


@dataclass(frozen=True)
class PrewatchState:
    status: PrewatchStatus
    words: tuple[PreviewWord, ...]
    guide: PrewatchGuide | None
    error: str | None = None


def _key(ref: VideoRef) -> str:
    return f"{ref.platform}/{ref.external_id}"


class PrewatchService:
    def __init__(
        self,
        videos: VideoService,
        vocabulary: VocabularyService,
        repo: PrewatchRepository,
        llm: LanguageModelProvider,
        tasks: TaskSupervisor,
        settings: SettingsStore,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._videos = videos
        self._vocabulary = vocabulary
        self._repo = repo
        self._llm = llm
        self._tasks = tasks
        self._settings = settings
        self._now = now
        self._jobs: dict[str, TaskHandle[None]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ queries
    def state(self, ref: VideoRef) -> PrewatchState:
        video = self._videos.require_video(ref)
        vocab = self._vocabulary.for_video(ref)
        glosses = self._repo.glosses(video.id)
        guide = self._repo.guide(video.id)
        words = tuple(
            PreviewWord(w, (vocab.examples(w.lemma, 1) or [""])[0], glosses.get(w.lemma))
            for w in preview_candidates(vocab.profile, PREVIEW_WORDS)
        )
        with self._lock:
            job = self._jobs.get(_key(ref))
        if job is not None and not job.state.finished:
            return PrewatchState(PrewatchStatus.RUNNING, words, guide)
        if guide is not None and all(w.gloss for w in words):
            return PrewatchState(PrewatchStatus.READY, words, guide)
        if job is not None and job.state is not TaskState.SUCCEEDED:
            return PrewatchState(PrewatchStatus.FAILED, words, guide, _job_error(job))
        if self._llm.current() is None:
            return PrewatchState(PrewatchStatus.NOT_CONFIGURED, words, guide)
        return PrewatchState(PrewatchStatus.IDLE, words, guide)

    # ------------------------------------------------------------ commands
    def start(self, ref: VideoRef, *, auto: bool) -> PrewatchState:
        """Generate glosses + guide in the background. `auto` = triggered by
        opening a video rather than by the learner pressing a button."""
        current = self.state(ref)
        if current.status in (PrewatchStatus.RUNNING, PrewatchStatus.READY):
            return current
        if current.status is PrewatchStatus.NOT_CONFIGURED:
            return current
        if auto and (
            not self._settings.current().auto_prewatch or current.status is PrewatchStatus.FAILED
        ):
            return current  # auto never retries a failure: that would re-bill on every reload
        handle = self._tasks.run_blocking(
            f"prewatch:{ref.external_id}", lambda: self._generate(ref), timeout=GENERATE_TIMEOUT_S
        )
        with self._lock:
            self._jobs[_key(ref)] = handle
        return self.state(ref)

    def prefetch_gloss(self, ref: VideoRef, lemma: str) -> None:
        """Make sure a collected word has a meaning for its exercises: fetch one
        in the background if no video has glossed it yet and an AI is set up."""
        if self._repo.latest_gloss(lemma) is not None or self._llm.current() is None:
            return
        self._tasks.run_blocking(
            f"gloss:{lemma}", lambda: self.gloss_word(ref, lemma), timeout=GENERATE_TIMEOUT_S
        )

    def gloss_word(self, ref: VideoRef, lemma: str) -> Gloss:
        """Gloss one word the learner clicked (blocking; run it through the task supervisor)."""
        video = self._videos.require_video(ref)
        cached = self._repo.glosses(video.id).get(lemma)
        if cached is not None:
            return cached
        model = self._require_model()
        vocab = self._vocabulary.for_video(ref)
        examples = vocab.examples(lemma, 2)
        prompt = _glosses_prompt([(lemma, examples)])
        data = model.generate_json(
            system=SYSTEM, prompt=prompt, schema=GLOSSES_SCHEMA, max_tokens=4000
        )
        glosses = _parse_glosses(data, {lemma: (examples or [""])[0]})
        if lemma not in glosses:
            raise LlmOutputError(f"no gloss for {lemma!r} in the answer")
        self._repo.save_glosses(video.id, [glosses[lemma]], model.model_name, self._now())
        return glosses[lemma]

    # ------------------------------------------------------------ the job
    def _generate(self, ref: VideoRef) -> None:
        model = self._require_model()
        video = self._videos.require_video(ref)
        vocab = self._vocabulary.for_video(ref)
        words = preview_candidates(vocab.profile, PREVIEW_WORDS)
        examples = {w.lemma: vocab.examples(w.lemma, 2) for w in words}
        transcript, partial = _transcript_text(vocab)
        prompt = (
            f"Video title: {video.title or '(untitled)'}\n\n"
            f"Transcript{' (first part only)' if partial else ''}:\n{transcript}\n\n"
            + (
                _glosses_prompt([(w.lemma, examples[w.lemma]) for w in words])
                if words
                else "There are no words to gloss: return an empty glosses list."
            )
            + "\n\nThen help the learner watch with a purpose:\n"
            "- prediction_prompt: one short Chinese sentence asking the learner to predict, "
            "from the title alone, what the video will say (do not reveal the answer).\n"
            "- questions: exactly 3 English comprehension questions, in the order the video "
            "answers them, each answerable from the video in a sentence; hint_zh says in "
            "Chinese what to listen for, without giving the answer."
        )
        data = model.generate_json(
            system=SYSTEM, prompt=prompt, schema=PREWATCH_SCHEMA, max_tokens=8000
        )
        try:
            out = _PrewatchOut.model_validate(data)
            guide = PrewatchGuide(
                out.prediction_prompt.strip(),
                tuple(Question(q.question.strip(), q.hint_zh.strip()) for q in out.questions[:5]),
                partial,
            )
        except (ValidationError, ValueError) as exc:
            raise LlmOutputError(f"unusable pre-watch answer: {exc}") from exc
        glosses = _parse_glosses(data, {w: (ex or [""])[0] for w, ex in examples.items()})
        now = self._now()
        self._repo.save_glosses(video.id, list(glosses.values()), model.model_name, now)
        self._repo.save_guide(video.id, guide, model.model_name, now)
        log.info(
            "pre-watch ready for %s: %d glosses, %d questions",
            ref.external_id,
            len(glosses),
            len(guide.questions),
        )

    def _require_model(self) -> LanguageModel:
        model = self._llm.current()
        if model is None:
            raise LlmNotConfiguredError("no API key configured")
        return model


def _glosses_prompt(words: list[tuple[str, list[str]]]) -> str:
    lines = ["Gloss these words as they are used in the video (lemma, then the lines that use it):"]
    for lemma, examples in words:
        quoted = " | ".join(f'"{e}"' for e in examples) or "(no example)"
        lines.append(f"- {lemma}: {quoted}")
    lines.append(
        "For each word give part_of_speech (n./v./adj./adv./phr.), definition_en (simple English, "
        "under 15 words) and definition_zh (a short Chinese gloss for this sense). Keep the lemma "
        "exactly as given."
    )
    return "\n".join(lines)


def _parse_glosses(data: dict[str, Any], examples: dict[str, str]) -> dict[str, Gloss]:
    try:
        out = _GlossesOut.model_validate({"glosses": data.get("glosses", [])})
    except ValidationError as exc:
        raise LlmOutputError(f"unusable glosses: {exc}") from exc
    glosses: dict[str, Gloss] = {}
    for g in out.glosses:
        lemma = g.lemma.strip().lower()
        if lemma not in examples:
            continue  # never store a gloss for a word we did not ask about
        try:
            glosses[lemma] = Gloss(
                lemma,
                g.part_of_speech.strip(),
                g.definition_en.strip(),
                g.definition_zh.strip(),
                examples[lemma],
            )
        except ValueError:
            continue
    return glosses


def _transcript_text(vocab: VideoVocabulary) -> tuple[str, bool]:
    lines: list[str] = []
    words = 0
    for seg, toks in zip(vocab.transcript.segments, vocab.tokens, strict=True):
        if words + len(toks) > MAX_TRANSCRIPT_WORDS:
            return "\n".join(lines), True
        lines.append(seg.text)
        words += len(toks)
    return "\n".join(lines), False


def _job_error(job: TaskHandle[None]) -> str:
    try:
        job.result(timeout=0)
    except LlmError as exc:
        return exc.user_message
    except Exception:  # noqa: BLE001 -- any other failure: generic message, details in the log
        return "生成预习内容失败，请稍后重试。"
    return "生成预习内容失败，请稍后重试。"
