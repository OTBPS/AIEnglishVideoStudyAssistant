"""The recognition worker protocol: the client against scripted workers, and
the worker itself against a fake faster-whisper."""

from __future__ import annotations

import io
import json
import sys
import textwrap
import threading
import time
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest

from videotutor.app.errors import Cancelled, GpuUnavailableError, SpeechError
from videotutor.app.ports import RecognitionRequest
from videotutor.infra.asr import worker
from videotutor.infra.asr.recognizer import WorkerRecognizer

SCRIPT = textwrap.dedent(
    """
    import json, sys, time
    mode = sys.argv[1]
    request = json.loads(sys.stdin.readline())
    def emit(**m): print(json.dumps(m), flush=True)
    if mode == "ok":
        emit(event="loaded", device=request["device"])
        emit(event="started", duration_ms=2000)
        print("some native library chatter", flush=True)
        emit(event="segment", end_ms=900, words=[[0, 400, " Hello"], [400, 900, " world."], "junk"])
        emit(event="done")
    elif mode == "load-error":
        emit(event="error", stage="load", message="CUDA driver too old")
    elif mode == "run-error":
        emit(event="loaded", device=request["device"])
        emit(event="error", stage="run", message="bad audio")
    elif mode == "crash":
        print("Traceback: segfault-ish", file=sys.stderr, flush=True)
        sys.exit(5)
    elif mode == "hang":
        emit(event="loaded", device="cpu")
        time.sleep(60)
    elif mode == "selftest":
        emit(event="done", versions={"x": "1"})
    """
)


def recognizer(tmp_path: Path, mode: str) -> WorkerRecognizer:
    script = tmp_path / "fake_worker.py"
    script.write_text(SCRIPT, encoding="utf-8")
    return WorkerRecognizer([sys.executable, str(script), mode])


def request(device: str = "cpu") -> RecognitionRequest:
    return RecognitionRequest(
        Path("a.m4s"), Path("model"), device, Path("rt") if device == "cuda" else None
    )


def test_words_and_progress_come_back(tmp_path: Path) -> None:
    seen: list[tuple[int, int]] = []
    words = recognizer(tmp_path, "ok").recognize(
        request(), lambda p, d: seen.append((p, d)), threading.Event()
    )
    assert [w.text for w in words] == [" Hello", " world."]
    assert seen == [(0, 2000), (900, 2000)]


def test_failures_are_classified(tmp_path: Path) -> None:
    with pytest.raises(GpuUnavailableError, match="driver too old"):
        recognizer(tmp_path, "load-error").recognize(
            request("cuda"), lambda p, d: None, threading.Event()
        )
    with pytest.raises(SpeechError) as exc:
        recognizer(tmp_path, "load-error").recognize(
            request("cpu"), lambda p, d: None, threading.Event()
        )
    assert not isinstance(exc.value, GpuUnavailableError)
    with pytest.raises(SpeechError, match="bad audio"):
        recognizer(tmp_path, "run-error").recognize(
            request("cuda"), lambda p, d: None, threading.Event()
        )
    with pytest.raises(SpeechError, match=r"code 5.*segfault"):
        recognizer(tmp_path, "crash").recognize(request(), lambda p, d: None, threading.Event())
    with pytest.raises(SpeechError, match="cannot start"):
        WorkerRecognizer([str(tmp_path / "missing.exe")]).recognize(
            request(), lambda p, d: None, threading.Event()
        )


def test_stop_kills_the_worker(tmp_path: Path) -> None:
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    started = time.monotonic()
    with pytest.raises(Cancelled):
        recognizer(tmp_path, "hang").recognize(request(), lambda p, d: None, stop)
    assert time.monotonic() - started < 10


def test_selftest(tmp_path: Path) -> None:
    assert recognizer(tmp_path, "selftest").selftest() == {"x": "1"}
    with pytest.raises(SpeechError):
        recognizer(tmp_path, "crash").selftest()


# ---------------------------------------------------------------- the worker itself
class _FakeWord:
    def __init__(self, start: float, end: float, word: str) -> None:
        self.start, self.end, self.word = start, end, word


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str, words: list[_FakeWord] | None) -> None:
        self.start, self.end, self.text, self.words = start, end, text, words


class FakeWhisperModel:
    fail_load = False
    fail_run = False
    created: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, path: str, **kwargs: Any) -> None:
        if FakeWhisperModel.fail_load:
            raise RuntimeError("Library cublas64_12.dll is not found")
        FakeWhisperModel.created.append({"path": path, **kwargs})

    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[Any, Any]:
        if FakeWhisperModel.fail_run and isinstance(audio, str):
            raise RuntimeError("Invalid data found when processing input")
        segs = [
            _FakeSegment(0.0, 1.0, " Hi.", [_FakeWord(0.0, 1.0, " Hi.")]),
            _FakeSegment(1.5, 2.0, " No words", None),
        ]
        return iter(segs), types.SimpleNamespace(duration=2.0)


@pytest.fixture
def fake_whisper(monkeypatch: pytest.MonkeyPatch) -> type[FakeWhisperModel]:
    FakeWhisperModel.fail_load = FakeWhisperModel.fail_run = False
    FakeWhisperModel.created = []
    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeWhisperModel)
    )
    monkeypatch.setattr(worker, "_use_dlls", lambda d: None)
    return FakeWhisperModel


def _run(request: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    worker._recognize(request, lambda **m: out.append(m))
    return out


def test_worker_protocol(fake_whisper: type[FakeWhisperModel]) -> None:
    events = _run({"audio": "a.m4s", "model_dir": "m", "device": "cpu"})
    assert [e["event"] for e in events] == ["loaded", "started", "segment", "segment", "done"]
    assert events[1]["duration_ms"] == 2000
    assert events[2]["words"] == [[0, 1000, " Hi."]]
    assert events[3]["words"] == [[1500, 2000, " No words"]]  # no word timings: the whole segment
    assert fake_whisper.created[0]["compute_type"] == "int8"

    events = _run({"audio": "a.m4s", "model_dir": "m", "device": "cuda", "gpu_runtime_dir": "rt"})
    assert events[0] == {"event": "loaded", "device": "cuda"}
    assert fake_whisper.created[-1]["compute_type"] == "float16"


def test_worker_reports_load_and_run_errors(fake_whisper: type[FakeWhisperModel]) -> None:
    fake_whisper.fail_load = True
    [event] = _run({"audio": "a", "model_dir": "m", "device": "cuda", "gpu_runtime_dir": "rt"})
    assert event["stage"] == "load" and "cublas" in event["message"]
    fake_whisper.fail_load, fake_whisper.fail_run = False, True
    events = _run({"audio": "a", "model_dir": "m", "device": "cpu"})
    assert events[-1]["stage"] == "run"


def test_worker_main_reads_one_request(
    monkeypatch: pytest.MonkeyPatch, fake_whisper: type[FakeWhisperModel]
) -> None:
    out = io.StringIO()
    monkeypatch.setattr(worker, "_streams", lambda: (io.StringIO('{"selftest": true}\n'), out))
    monkeypatch.setattr(worker, "_exit_with_parent", lambda pid: None)
    monkeypatch.setattr(worker, "_selftest", lambda emit: emit(event="done") or 0)
    assert worker.main() == 0
    assert json.loads(out.getvalue()) == {"event": "done"}

    out = io.StringIO()
    monkeypatch.setattr(worker, "_streams", lambda: (io.StringIO("not json\n"), out))
    assert worker.main() == 2
    assert json.loads(out.getvalue())["event"] == "error"


def test_worker_selftest_loads_the_real_libraries() -> None:
    out: list[dict[str, Any]] = []
    assert worker._selftest(lambda **m: out.append(m)) == 0
    assert set(out[0]["versions"]) == {"faster_whisper", "ctranslate2", "av"}
    assert 1 <= worker._cpu_threads() <= 8
