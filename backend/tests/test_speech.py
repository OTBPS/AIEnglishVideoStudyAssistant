"""Speech recognition: domain rules, the component store, setup and transcription jobs."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from videotutor.app.errors import (
    DiskFullError,
    InvalidInputError,
    NotFoundError,
    SpeechNotReadyError,
)
from videotutor.app.speech import InstallStatus, SpeechSetup
from videotutor.app.transcription import JobStatus, TranscriptionService, _timeout
from videotutor.core.tasks import TaskSupervisor
from videotutor.domain.speech import (
    Component,
    ComponentKind,
    RemoteFile,
    Word,
    choose_model,
    lines_from_words,
)
from videotutor.domain.video import Platform, TrackKind, VideoRef
from videotutor.infra.asr import catalog
from videotutor.infra.components_store import FileComponentStore
from videotutor.infra.db.database import Database
from videotutor.infra.hardware import parse_nvidia_smi

from .speech_fakes import (
    BIG,
    GOOD_GPU,
    MODELS,
    RUNTIME,
    SMALL,
    FakeDownloader,
    FakeHardware,
    FakeRecognizer,
    remote,
)
from .test_videos import make_service

SHA = "0" * 64


# ---------------------------------------------------------------- domain
def test_remote_files_are_pinned_and_https_only() -> None:
    RemoteFile("model.bin", 1, SHA, ("https://h/model.bin",))
    for bad in [
        dict(name="../x", size=1, sha256=SHA, urls=("https://h",)),
        dict(name="x", size=0, sha256=SHA, urls=("https://h",)),
        dict(name="x", size=1, sha256="abc", urls=("https://h",)),
        dict(name="x", size=1, sha256=SHA, urls=("http://h",)),
        dict(name="x", size=1, sha256=SHA, urls=()),
    ]:
        with pytest.raises(ValueError):
            RemoteFile(**bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Component("Bad Id", ComponentKind.SPEECH_MODEL, "", "", (remote("a"),))
    with pytest.raises(ValueError):
        Component("ok-id", ComponentKind.SPEECH_MODEL, "", "", ())


def test_the_real_catalog_is_valid() -> None:
    ids = [c.id for c in catalog.ALL]
    assert len(ids) == len(set(ids))
    assert [m.kind for m in catalog.SPEECH_MODELS] == [ComponentKind.SPEECH_MODEL] * 3
    assert catalog.GPU_RUNTIME.download_bytes > 1_000_000_000
    for c in catalog.ALL:
        for f in c.files:
            assert len(f.urls) == 2  # an origin and a mirror for every file


def test_choose_model_respects_the_device() -> None:
    assert choose_model([BIG, SMALL], gpu=True) is BIG
    assert choose_model([BIG, SMALL], gpu=False) is SMALL
    assert choose_model([BIG], gpu=False) is BIG  # slow, but better than nothing
    assert choose_model([RUNTIME], gpu=True) is None
    assert choose_model([], gpu=False) is None


def _words(
    text: str, start: int = 0, step: int = 300, gap_after: dict[int, int] | None = None
) -> list[Word]:
    out, t = [], start
    for i, w in enumerate(text.split()):
        out.append(Word(t, t + step - 50, " " + w))
        t += step + (gap_after or {}).get(i, 0)
    return out


def test_lines_end_at_sentences_and_pauses() -> None:
    words = _words("Hello there. How are you doing today", gap_after={4: 2000})
    lines = [c.text for c in lines_from_words(words)]
    assert lines == ["Hello there.", "How are you", "doing today"]
    assert lines_from_words([Word(0, 10, "  ")]) == []


def test_long_lines_split_at_a_comma_or_before_the_overflowing_word() -> None:
    long_clause = "one two three four, " + " ".join(["word"] * 30)
    lines = [c.text for c in lines_from_words(_words(long_clause, step=100))]
    assert lines[0] == "one two three four,"
    assert all(len(line) <= 84 for line in lines)
    slow = lines_from_words(_words("a b c d e f g h i j k l m n o p q r s t u v", step=400))
    assert all(c.duration_ms <= 6000 for c in slow)
    assert " ".join(c.text for c in slow) == "a b c d e f g h i j k l m n o p q r s t u v"


def test_parse_nvidia_smi_picks_the_biggest_gpu() -> None:
    out = "NVIDIA GeForce GTX 1050, 2048, 537.13\nNVIDIA GeForce RTX 3060, 12288, 537.13\n"
    gpu = parse_nvidia_smi(out)
    assert gpu is not None and (gpu.name, gpu.memory_mb) == ("NVIDIA GeForce RTX 3060", 12288)
    assert parse_nvidia_smi("No devices were found\n") is None


# ---------------------------------------------------------------- component store
def test_store_installs_verifies_extracts_and_removes(tmp_path: Path) -> None:
    store = FileComponentStore(tmp_path)
    dl = FakeDownloader()
    seen: list[int] = []
    assert not store.is_installed(RUNTIME)
    store.install(RUNTIME, dl, seen.append, threading.Event())
    folder = store.directory(RUNTIME)
    assert sorted(p.name for p in folder.iterdir()) == ["installed.json", "lib_a.dll", "lib_b.dll"]
    assert store.is_installed(RUNTIME)
    assert seen[-1] == RUNTIME.download_bytes
    assert not (tmp_path / ".partial" / RUNTIME.id).exists()

    # Same id, different pinned files (a catalog update): no longer installed.
    changed = Component(RUNTIME.id, RUNTIME.kind, "", "", (remote("other.whl", archive=True),))
    assert not store.is_installed(changed)

    store.remove(RUNTIME)
    assert not folder.exists() and not store.is_installed(RUNTIME)
    assert store.free_bytes() > 0


def test_interrupted_install_resumes_and_never_looks_installed(tmp_path: Path) -> None:
    two = Component(
        "two-files", ComponentKind.SPEECH_MODEL, "", "", (remote("a.bin"), remote("b.bin"))
    )
    store = FileComponentStore(tmp_path)
    dl = FakeDownloader()
    dl.fail_names = {"b.bin"}
    with pytest.raises(Exception, match="boom"):
        store.install(two, dl, lambda n: None, threading.Event())
    assert not store.is_installed(two) and not store.directory(two).exists()
    dl.fail_names = set()
    dl.calls.clear()
    store.install(two, dl, lambda n: None, threading.Event())
    assert [c[1].name for c in dl.calls] == ["b.bin"]  # a.bin was kept from the first attempt
    assert store.is_installed(two)


def test_corrupt_marker_means_not_installed(tmp_path: Path) -> None:
    store = FileComponentStore(tmp_path)
    store.directory(SMALL).mkdir(parents=True)
    (store.directory(SMALL) / "installed.json").write_text("{not json", encoding="utf-8")
    assert not store.is_installed(SMALL)
    (store.directory(SMALL) / "installed.json").write_text(json.dumps([1]), encoding="utf-8")
    assert not store.is_installed(SMALL)


# ---------------------------------------------------------------- setup service
@pytest.fixture
def tasks() -> Iterator[TaskSupervisor]:
    t = TaskSupervisor(default_timeout=10)
    t.start()
    yield t
    t.shutdown(grace=2)


def _wait(cond: object, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():  # type: ignore[operator]
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def make_setup(
    tmp_path: Path, tasks: TaskSupervisor, gpu: object = None
) -> tuple[SpeechSetup, FileComponentStore, FakeDownloader]:
    store = FileComponentStore(tmp_path / "models")
    dl = FakeDownloader()
    hw = FakeHardware(gpu)  # type: ignore[arg-type]
    return SpeechSetup(MODELS, RUNTIME, store, dl, hw, tasks), store, dl


def _status(setup: SpeechSetup, cid: str) -> InstallStatus:
    return next(v.status for v in setup.overview().components if v.component.id == cid)


def test_cpu_machine_recommends_the_cpu_model_and_becomes_ready(
    tmp_path: Path, tasks: TaskSupervisor
) -> None:
    setup, _, _ = make_setup(tmp_path, tasks)
    o = setup.overview()
    assert not o.ready and setup.plan() is None
    assert {v.component.id for v in o.components if v.recommended} == {SMALL.id}
    setup.install(SMALL.id)
    _wait(lambda: _status(setup, SMALL.id) is InstallStatus.INSTALLED)
    plan = setup.plan()
    assert plan is not None and plan.model is SMALL and plan.device == "cpu"
    assert setup.install(SMALL.id).status is InstallStatus.INSTALLED  # idempotent


def test_gpu_machine_uses_the_gpu_until_it_fails(tmp_path: Path, tasks: TaskSupervisor) -> None:
    setup, _, _ = make_setup(tmp_path, tasks, GOOD_GPU)
    assert {v.component.id for v in setup.overview().components if v.recommended} == {
        BIG.id,
        RUNTIME.id,
    }
    setup.install(BIG.id)
    setup.install(RUNTIME.id)
    _wait(lambda: setup.overview().gpu.usable)
    plan = setup.plan()
    assert plan is not None and plan.device == "cuda" and plan.gpu_runtime_dir is not None
    assert plan.on_cpu().device == "cpu"
    setup.gpu_failed("cuDNN missing")
    o = setup.overview()
    assert not o.gpu.usable and o.gpu.failure == "cuDNN missing"
    assert setup.plan().device == "cpu"  # type: ignore[union-attr]


def test_install_failures_cancel_and_remove(tmp_path: Path, tasks: TaskSupervisor) -> None:
    setup, store, dl = make_setup(tmp_path, tasks)
    dl.fail_names = {f"{SMALL.id}.bin"}
    setup.install(SMALL.id)
    _wait(lambda: _status(setup, SMALL.id) is InstallStatus.FAILED)
    view = next(v for v in setup.overview().components if v.component.id == SMALL.id)
    assert view.error and "下载失败" in view.error

    dl.fail_names = set()
    dl.gate = threading.Event()
    assert setup.install(SMALL.id).status is InstallStatus.INSTALLING
    with pytest.raises(InvalidInputError):
        setup.remove(SMALL.id)
    setup.cancel(SMALL.id)
    _wait(lambda: _status(setup, SMALL.id) is InstallStatus.NOT_INSTALLED)

    dl.gate = None
    setup.install(SMALL.id)
    _wait(lambda: store.is_installed(SMALL))
    assert setup.remove(SMALL.id).status is InstallStatus.NOT_INSTALLED
    with pytest.raises(NotFoundError):
        setup.install("nope")


def test_install_refuses_when_the_disk_is_full(
    tmp_path: Path, tasks: TaskSupervisor, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup, store, _ = make_setup(tmp_path, tasks)
    monkeypatch.setattr(store, "free_bytes", lambda: 1000)
    with pytest.raises(DiskFullError) as exc:
        setup.install(SMALL.id)
    assert "GB" in exc.value.user_message


# ---------------------------------------------------------------- transcription
BV = VideoRef(Platform.BILIBILI, "BV1xx411c7mD")
AUDIO = "https://upos-sz-mirrorcos.bilivideo.com/upgcxcode/1-30216.m4s?e=x"


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    db, _ = Database.open(tmp_path / "tutor.db")
    yield db
    db.close()


def make_transcription(
    tmp_path: Path,
    tasks: TaskSupervisor,
    database: Database,
    recognizer: FakeRecognizer,
    *,
    gpu: object = None,
) -> tuple[TranscriptionService, SpeechSetup, FakeDownloader]:
    videos = make_service(database)
    videos.open_video(BV, title="t", url="", duration_ms=60_000, tracks=[])
    setup, _, dl = make_setup(tmp_path, tasks, gpu)
    service = TranscriptionService(videos, setup, dl, recognizer, tasks, tmp_path / "cache")
    return service, setup, dl


def _install(setup: SpeechSetup, *ids: str) -> None:
    for cid in ids:
        setup.install(cid)
    _wait(lambda: all(_status(setup, c) is InstallStatus.INSTALLED for c in ids))


def _done(service: TranscriptionService, ref: VideoRef = BV) -> JobStatus:
    _wait(lambda: service.job(ref).status.finished)  # type: ignore[union-attr]
    return service.job(ref).status  # type: ignore[union-attr]


def test_transcription_stores_an_asr_transcript(
    tmp_path: Path, tasks: TaskSupervisor, database: Database
) -> None:
    rec = FakeRecognizer()
    service, setup, dl = make_transcription(tmp_path, tasks, database, rec)
    with pytest.raises(SpeechNotReadyError):
        service.start(BV, [AUDIO], 60_000)
    _install(setup, SMALL.id)
    assert service.job(BV) is None
    service.start(BV, [AUDIO, "https://evil.example/a.m4s", "http://x.bilivideo.com/a"], 60_000)
    assert _done(service) is JobStatus.DONE
    job = service.job(BV)
    assert job is not None and job.progress == 1.0 and job.model == SMALL.id and job.device == "cpu"
    assert job.transcript is not None and job.transcript.kind is TrackKind.ASR
    assert job.transcript.segment_count == 2 and job.transcript.source == f"local-{SMALL.id}"
    audio_call = dl.calls[-1]
    assert audio_call[0] == [AUDIO] and audio_call[2]["Referer"] == "https://www.bilibili.com/"
    assert not list((tmp_path / "cache").glob("*"))  # the audio was deleted


def test_transcription_rejects_foreign_urls_and_platforms(
    tmp_path: Path, tasks: TaskSupervisor, database: Database
) -> None:
    service, setup, _ = make_transcription(tmp_path, tasks, database, FakeRecognizer())
    _install(setup, SMALL.id)
    with pytest.raises(InvalidInputError):
        service.start(BV, ["https://evil.example/x.m4s"], None)
    yt = VideoRef(Platform.YOUTUBE, "smoketest00")
    with pytest.raises(NotFoundError):
        service.start(yt, [AUDIO], None)


def test_gpu_failure_falls_back_to_the_cpu(
    tmp_path: Path, tasks: TaskSupervisor, database: Database
) -> None:
    rec = FakeRecognizer()
    rec.gpu_broken = True
    service, setup, _ = make_transcription(tmp_path, tasks, database, rec, gpu=GOOD_GPU)
    _install(setup, BIG.id, RUNTIME.id)
    service.start(BV, [AUDIO], 60_000)
    assert _done(service) is JobStatus.DONE
    assert [r.device for r in rec.requests] == ["cuda", "cpu"]
    assert service.job(BV).device == "cpu"  # type: ignore[union-attr]
    assert setup.overview().gpu.failure == "no cuDNN"


def test_no_speech_and_cancel(tmp_path: Path, tasks: TaskSupervisor, database: Database) -> None:
    rec = FakeRecognizer(words=[])
    service, setup, _ = make_transcription(tmp_path, tasks, database, rec)
    _install(setup, SMALL.id)
    service.start(BV, [AUDIO], 60_000)
    assert _done(service) is JobStatus.FAILED
    assert "没有识别出英文语音" in (service.job(BV).error or "")  # type: ignore[union-attr]

    rec.words, rec.block = [Word(0, 100, " Hi.")], True
    first = service.start(BV, [AUDIO], 60_000)
    assert first.status is JobStatus.QUEUED
    assert service.start(BV, [AUDIO], 60_000).status is not JobStatus.DONE  # same job, not a second
    _wait(lambda: service.job(BV).status is JobStatus.RECOGNIZING)  # type: ignore[union-attr]
    service.cancel(BV)
    assert _done(service) is JobStatus.CANCELLED
    assert service.cancel(VideoRef(Platform.BILIBILI, "BV1yy411c7mD")) is None


def test_jobs_wait_their_turn(tmp_path: Path, tasks: TaskSupervisor, database: Database) -> None:
    rec = FakeRecognizer()
    rec.block = True
    service, setup, _ = make_transcription(tmp_path, tasks, database, rec)
    other = VideoRef(Platform.BILIBILI, "BV1yy411c7mD")
    service._videos.open_video(other, title="o", url="", duration_ms=None, tracks=[])
    _install(setup, SMALL.id)
    service.start(BV, [AUDIO], 60_000)
    _wait(lambda: service.job(BV).status is JobStatus.RECOGNIZING)  # type: ignore[union-attr]
    service.start(other, [AUDIO], None)
    time.sleep(0.2)
    assert service.job(other).status is JobStatus.QUEUED  # type: ignore[union-attr]
    service.cancel(other)
    assert _done(service, other) is JobStatus.CANCELLED
    rec.block = False
    assert _done(service) is JobStatus.DONE


def test_timeouts_scale_with_the_video() -> None:
    assert _timeout(None) > _timeout(3_600_000) > _timeout(60_000) > 900
