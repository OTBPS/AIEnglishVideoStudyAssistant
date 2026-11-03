"""Client side of the recognition worker (see worker.py for the protocol)."""

from __future__ import annotations

import collections
import json
import logging
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from videotutor.app.errors import Cancelled, GpuUnavailableError, SpeechError
from videotutor.app.ports import RecognitionRequest
from videotutor.domain.speech import Word

log = logging.getLogger("asr")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
STDERR_TAIL = 40
SELFTEST_TIMEOUT_S = 120.0


class WorkerRecognizer:
    def __init__(self, command: list[str], cwd: Path | None = None) -> None:
        self._command = command
        self._cwd = cwd

    def recognize(
        self,
        request: RecognitionRequest,
        progress: Callable[[int, int], None],
        stop: threading.Event,
    ) -> list[Word]:
        message = {
            "audio": str(request.audio),
            "model_dir": str(request.model_dir),
            "device": request.device,
            "gpu_runtime_dir": str(request.gpu_runtime_dir) if request.gpu_runtime_dir else None,
        }
        words: list[Word] = []
        duration_ms = 0
        loaded = False
        for event in self._run(message, stop):
            kind = event.get("event")
            if kind == "loaded":
                loaded = True
                log.info("recognizer running on %s", event.get("device"))
            elif kind == "started":
                duration_ms = int(event.get("duration_ms") or 0)
                progress(0, duration_ms)
            elif kind == "segment":
                words.extend(_words(event.get("words")))
                progress(int(event.get("end_ms") or 0), duration_ms)
            elif kind == "done":
                return words
            elif kind == "error":
                text = str(event.get("message", ""))[:2000]
                if request.device == "cuda" and not loaded:
                    raise GpuUnavailableError(text)
                raise SpeechError(text)
        raise SpeechError("the recognition worker ended without an answer")

    def selftest(self) -> dict[str, Any]:
        """Start a worker and check its native libraries load (packaged smoke test)."""
        stop = threading.Event()
        timer = threading.Timer(SELFTEST_TIMEOUT_S, stop.set)
        timer.start()
        try:
            for event in self._run({"selftest": True}, stop):
                if event.get("event") == "done":
                    return dict(event.get("versions") or {})
                if event.get("event") == "error":
                    raise SpeechError(str(event.get("message")))
        finally:
            timer.cancel()
        raise SpeechError("selftest: no answer")

    # ------------------------------------------------------------ process
    def _run(self, message: dict[str, Any], stop: threading.Event) -> Any:
        """Yield the worker's events. The process is killed when `stop` is set
        or the caller stops reading; it never outlives this generator."""
        try:
            proc = subprocess.Popen(
                self._command,
                cwd=self._cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW,
            )
        except OSError as exc:
            raise SpeechError(f"cannot start the recognition worker: {exc}") from exc
        if proc.stdin is None or proc.stdout is None or proc.stderr is None:  # pragma: no cover
            raise SpeechError("worker pipes missing")
        tail: collections.deque[str] = collections.deque(maxlen=STDERR_TAIL)
        finished = threading.Event()
        threading.Thread(target=_drain, args=(proc.stderr, tail), daemon=True).start()
        threading.Thread(target=_kill_on, args=(proc, stop, finished), daemon=True).start()
        try:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.close()
            for line in proc.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # stray output from a native library
                if isinstance(event, dict):
                    yield event
            code = proc.wait()
            if stop.is_set():
                raise Cancelled("recognition")
            detail = "".join(tail).strip()[-1500:]
            log.error("recognition worker exited with %s: %s", code, detail)
            raise SpeechError(f"worker exited with code {code}: {detail}")
        except OSError as exc:  # broken pipe: the worker died early
            if stop.is_set():
                raise Cancelled("recognition") from exc
            raise SpeechError(f"worker died: {exc}; {''.join(tail)[-1500:]}") from exc
        finally:
            finished.set()
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def _words(raw: Any) -> list[Word]:
    out: list[Word] = []
    for item in raw or []:
        if isinstance(item, list) and len(item) == 3 and isinstance(item[2], str):
            start, end = int(item[0]), int(item[1])
            if 0 <= start <= end:
                out.append(Word(start, end, item[2]))
    return out


def _drain(stream: Any, tail: collections.deque[str]) -> None:
    for line in stream:
        tail.append(line)


def _kill_on(proc: subprocess.Popen[str], stop: threading.Event, finished: threading.Event) -> None:
    while not finished.is_set():
        if stop.wait(0.2):
            proc.kill()
            return
