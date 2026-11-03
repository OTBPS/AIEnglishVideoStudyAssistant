"""Pre-watch and AI-settings use cases on the real database and task supervisor,
with a fake language model."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from videotutor.app.errors import (
    InvalidInputError,
    LlmAuthError,
    LlmNotConfiguredError,
    LlmOutputError,
)
from videotutor.app.llm_settings import LlmSettingsService, secret_name
from videotutor.app.prewatch import PrewatchService, PrewatchState, PrewatchStatus
from videotutor.app.videos import VideoService
from videotutor.app.vocabulary import VocabularyService
from videotutor.core.tasks import TaskSupervisor
from videotutor.domain.video import CaptionTrack, Platform, TrackKind, VideoRef
from videotutor.domain.vocab import WordStatus
from videotutor.infra.db.database import Database
from videotutor.infra.db.repositories import SqlitePrewatchRepository, SqliteWordStatusRepository
from videotutor.infra.settings_store import JsonSettingsStore

from .conftest import FakeLanguageModel, FixedModelProvider, InMemorySecretStore
from .test_videos import json3, make_service
from .test_vocab import LEXICON

REF = VideoRef(Platform.YOUTUBE, "iG9CE55wbtY")
TRACK = CaptionTrack(".en", "en", TrackKind.MANUAL)
LINES = (
    (0, 3000, "Creativity is as important as literacy."),
    (3000, 3000, "We are educating people out of their creativity."),
    (6000, 3000, "Kids will take a chance; they are not frightened of being wrong."),
    (9000, 3000, "And as"),
    (12000, 3000, "Robinson said, the hierarchy of subjects is the same everywhere."),
    (15000, 3000, "Gillian could not sit still."),
)


def answer(*lemmas: str) -> dict[str, Any]:
    return {
        "glosses": [
            {
                "lemma": lemma,
                "part_of_speech": "n.",
                "definition_en": f"meaning of {lemma}",
                "definition_zh": f"{lemma} 的意思",
            }
            for lemma in lemmas
        ],
        "prediction_prompt": "看标题，你觉得演讲者会怎么看学校？",
        "questions": [{"question": f"Q{i}?", "hint_zh": f"注意第 {i} 部分"} for i in range(1, 4)],
    }


class Env:
    def __init__(self, tmp: Path, model: FakeLanguageModel | None) -> None:
        self.db, _ = Database.open(tmp / "tutor.db")
        self.settings = JsonSettingsStore(tmp / "settings.json")
        self.settings.load()
        self.settings.update(vocabulary_level=3000)
        self.tasks = TaskSupervisor(default_timeout=10)
        self.tasks.start()
        self.videos: VideoService = make_service(self.db)
        self.vocabulary = VocabularyService(
            LEXICON, self.videos, SqliteWordStatusRepository(self.db), self.settings
        )
        self.provider = FixedModelProvider(model)
        self.prewatch = PrewatchService(
            self.videos,
            self.vocabulary,
            SqlitePrewatchRepository(self.db),
            self.provider,
            self.tasks,
            self.settings,
        )
        self.videos.open_video(
            REF, title="Do schools kill creativity?", url="", duration_ms=12000, tracks=[TRACK]
        )
        self.videos.store_captions(REF, TRACK, "json3", json3(*LINES))

    def wait(self) -> PrewatchState:
        for _ in range(200):
            s = self.prewatch.state(REF)
            if s.status is not PrewatchStatus.RUNNING:
                return s
            time.sleep(0.02)
        raise AssertionError("still running")

    def close(self) -> None:
        self.tasks.shutdown(grace=2)
        self.db.close()


@pytest.fixture
def make_env(tmp_path: Path) -> Iterator[Any]:
    envs: list[Env] = []

    def make(model: FakeLanguageModel | None) -> Env:
        env = Env(tmp_path / str(len(envs)), model)
        envs.append(env)
        return env

    yield make
    for env in envs:
        env.close()


def test_vocabulary_for_a_video(make_env: Any) -> None:
    env = make_env(None)
    v = env.vocabulary.for_video(REF)
    lemmas = {w.lemma for w in v.profile.words}
    assert "robinson" in v.names and "robinson" not in lemmas  # mid-sentence across segments
    gillian = next(w for w in v.profile.words if w.lemma == "gillian")
    assert gillian.always_capitalized  # undecidable, but never offered for preview
    assert "gillian" not in {w.word.lemma for w in env.prewatch.state(REF).words}
    unknown = {w.lemma for w in v.profile.unknown}
    assert {"creativity", "literacy", "hierarchy"} <= unknown
    assert v.examples("creativity") == [LINES[0][2], LINES[1][2]]

    env.vocabulary.set_status("literacy", WordStatus.KNOWN)
    assert "literacy" not in {w.lemma for w in env.vocabulary.for_video(REF).profile.unknown}
    env.vocabulary.set_status("literacy", None)
    assert "literacy" in {w.lemma for w in env.vocabulary.for_video(REF).profile.unknown}
    with pytest.raises(InvalidInputError):
        env.vocabulary.set_status("Not A Word", WordStatus.KNOWN)


def test_prewatch_generates_once_and_is_cached(make_env: Any) -> None:
    model = FakeLanguageModel(
        answer("creativity", "literacy", "hierarchy", "frighten", "educate", "invented")
    )
    env = make_env(model)
    idle = env.prewatch.state(REF)
    assert idle.status is PrewatchStatus.IDLE
    asked = [w.word.lemma for w in idle.words]
    assert asked[0] == "creativity"  # used twice in the video

    env.prewatch.start(REF, auto=True)
    ready = env.wait()
    assert ready.status is PrewatchStatus.READY, ready.error
    assert ready.guide is not None and len(ready.guide.questions) == 3
    glossed = {w.word.lemma: w.gloss for w in ready.words}
    assert all(glossed[lemma] is not None for lemma in asked)
    creativity = glossed["creativity"]
    assert creativity is not None and creativity.example == LINES[0][2]  # from the transcript

    prompt = str(model.calls[0]["prompt"])
    assert "Do schools kill creativity?" in prompt and LINES[4][2] in prompt
    env.prewatch.start(REF, auto=False)
    assert len(model.calls) == 1, "a ready video is never generated twice"


def test_unrequested_glosses_are_dropped(make_env: Any) -> None:
    env = make_env(FakeLanguageModel(answer("invented")))
    env.prewatch.start(REF, auto=False)
    s = env.wait()
    assert s.status is PrewatchStatus.IDLE  # guide stored, but no gloss for the words we asked
    assert s.guide is not None and all(w.gloss is None for w in s.words)


def test_not_configured_and_auto_switch(make_env: Any) -> None:
    env = make_env(None)
    assert env.prewatch.start(REF, auto=False).status is PrewatchStatus.NOT_CONFIGURED

    model = FakeLanguageModel(answer())
    env.provider.model = model
    env.settings.update(auto_prewatch=False)
    assert env.prewatch.start(REF, auto=True).status is PrewatchStatus.IDLE
    assert model.calls == []


def test_failures_are_reported_and_not_auto_retried(make_env: Any) -> None:
    model = FakeLanguageModel(LlmAuthError("401"), {"glosses": "nope"}, answer())
    env = make_env(model)
    env.prewatch.start(REF, auto=True)
    failed = env.wait()
    assert failed.status is PrewatchStatus.FAILED
    assert failed.error == LlmAuthError.user_message
    env.prewatch.start(REF, auto=True)
    assert len(model.calls) == 1, "auto never re-bills a failure"

    env.prewatch.start(REF, auto=False)  # the learner pressed retry: malformed answer
    assert env.wait().status is PrewatchStatus.FAILED


def test_gloss_one_clicked_word(make_env: Any) -> None:
    model = FakeLanguageModel(
        {
            "glosses": [
                {
                    "lemma": "Hierarchy",
                    "part_of_speech": "n.",
                    "definition_en": "an order of rank",
                    "definition_zh": "等级制度",
                }
            ]
        },
        {"glosses": []},
    )
    env = make_env(model)
    g = env.prewatch.gloss_word(REF, "hierarchy")
    assert g.definition_zh == "等级制度" and g.example == LINES[4][2]
    assert env.prewatch.gloss_word(REF, "hierarchy") == g  # cached
    with pytest.raises(LlmOutputError):
        env.prewatch.gloss_word(REF, "literacy")
    env.provider.model = None
    with pytest.raises(LlmNotConfiguredError):
        env.prewatch.gloss_word(REF, "frighten")


# ---------------------------------------------------------------- AI settings
def test_llm_settings_store_key_securely_and_test(tmp_path: Path) -> None:
    settings = JsonSettingsStore(tmp_path / "settings.json")
    settings.load()
    secrets = InMemorySecretStore()
    provider = FixedModelProvider(None)
    svc = LlmSettingsService(settings, secrets, provider)
    assert svc.config().has_key is False
    with pytest.raises(LlmNotConfiguredError):
        svc.test()

    cfg = svc.configure("claude", "claude-opus-5-5", "", "  sk-ant-123  ")
    assert cfg.has_key and secrets.values[secret_name("claude")] == "sk-ant-123"
    assert "sk-ant" not in (tmp_path / "settings.json").read_text(encoding="utf-8")

    provider.model = FakeLanguageModel({"ok": True})
    svc.test()
    assert provider.model.calls

    for bad in [
        ("claude", " ", "", None),
        ("openai_compatible", "m", "http://insecure", None),
        ("claude", "m", "", "has space"),
    ]:
        with pytest.raises(InvalidInputError):
            svc.configure(*bad)  # type: ignore[arg-type]
    assert svc.remove_key().has_key is False
