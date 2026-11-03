"""The speech-recognition worker: `VideoTutor.exe --asr-worker` (or
`python -m videotutor --asr-worker`), started by recognizer.py.

Recognition runs in its own process so that it can be stopped instantly (the
parent kills it), a GPU driver crash cannot take the tray app down, and the
model's memory is returned to the system as soon as it is done.

Protocol: the parent writes one JSON request line to stdin and closes it;
the worker answers with JSON lines on stdout:

    {"event": "loaded", "device": "cuda"}
    {"event": "started", "duration_ms": 612000}
    {"event": "segment", "end_ms": 4200, "words": [[0, 380, " Hello"], ...]}
    {"event": "done"}
    {"event": "error", "stage": "load" | "run", "message": "..."}

If the parent process ends (quit or crashed), the worker exits at once.
Only this module imports faster-whisper; the app itself never loads it.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import IO, Any

SAMPLE_RATE = 16000
SYNCHRONIZE = 0x00100000
INFINITE = 0xFFFFFFFF


def main() -> int:
    stdin, stdout = _streams()
    emit = _emitter(stdout)
    try:
        request = json.loads(stdin.readline() or "{}")
    except ValueError as exc:
        emit(event="error", stage="load", message=f"bad request: {exc}")
        return 2
    threading.Thread(target=_exit_with_parent, args=(os.getppid(),), daemon=True).start()
    if request.get("selftest"):
        return _selftest(emit)
    return _recognize(request, emit)


def _recognize(request: dict[str, Any], emit: Any) -> int:
    device = str(request.get("device", "cpu"))
    try:
        if request.get("gpu_runtime_dir"):
            _use_dlls(str(request["gpu_runtime_dir"]))
        from faster_whisper import WhisperModel

        model = WhisperModel(
            str(request["model_dir"]),
            device=device,
            compute_type="float16" if device == "cuda" else "int8",
            cpu_threads=_cpu_threads(),
        )
        if device == "cuda":
            # Loading can succeed and the first real computation still fail
            # (missing cuDNN pieces, too old a driver): find out now, while
            # falling back to the CPU is still cheap.
            import numpy as np

            list(model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en")[0])
    except Exception as exc:  # noqa: BLE001 -- reported to the parent, which decides
        emit(event="error", stage="load", message=f"{type(exc).__name__}: {exc}")
        return 1
    emit(event="loaded", device=device)
    try:
        segments, info = model.transcribe(
            str(request["audio"]),
            language="en",
            beam_size=5,
            vad_filter=True,  # skips music and silence, where Whisper invents text
            word_timestamps=True,
            condition_on_previous_text=False,  # one bad line must not poison the rest
        )
        emit(event="started", duration_ms=round(info.duration * 1000))
        for seg in segments:
            words = [[round(w.start * 1000), round(w.end * 1000), w.word] for w in seg.words or []]
            if not words:
                words = [[round(seg.start * 1000), round(seg.end * 1000), seg.text]]
            emit(event="segment", end_ms=round(seg.end * 1000), words=words)
    except Exception as exc:  # noqa: BLE001
        emit(event="error", stage="run", message=f"{type(exc).__name__}: {exc}")
        return 1
    emit(event="done")
    return 0


def _selftest(emit: Any) -> int:
    """Used by the packaged smoke test: every native piece loads."""
    try:
        import av
        import ctranslate2
        import faster_whisper
        from faster_whisper.vad import get_vad_model

        get_vad_model()  # the bundled Silero model, through onnxruntime
        emit(
            event="done",
            versions={
                "faster_whisper": faster_whisper.__version__,
                "ctranslate2": ctranslate2.__version__,
                "av": av.__version__,
            },
        )
    except Exception as exc:  # noqa: BLE001
        emit(event="error", stage="load", message=f"{type(exc).__name__}: {exc}")
        return 1
    return 0


# ---------------------------------------------------------------- plumbing
def _streams() -> tuple[IO[str], IO[str]]:
    # A windowed exe may start without sys.stdin/sys.stdout objects even
    # though the parent handed it pipes: open the descriptors directly.
    stdin = sys.stdin or open(0, encoding="utf-8", closefd=False)  # noqa: SIM115
    stdout = sys.stdout or open(1, "w", encoding="utf-8", closefd=False)  # noqa: SIM115
    for s in (stdin, stdout):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    return stdin, stdout


def _emitter(stdout: IO[str]) -> Any:
    lock = threading.Lock()

    def emit(**message: Any) -> None:
        with lock:
            stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
            stdout.flush()

    return emit


def _exit_with_parent(parent_pid: int) -> None:
    # Not by watching stdin: on Windows a thread blocked reading a pipe stalls
    # every other call on that handle (isatty checks in imported libraries).
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
        if handle:
            kernel32.WaitForSingleObject(handle, INFINITE)
    else:  # pragma: no cover -- the product is Windows-only; keeps tests portable
        while _alive(parent_pid):
            time.sleep(1)
    os._exit(3)  # parent gone: nobody wants the result


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _use_dlls(directory: str) -> None:
    # cuDNN loads its sub-libraries by name at run time, through the normal
    # search path; add_dll_directory alone only covers direct imports.
    os.add_dll_directory(directory)
    os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")


def _cpu_threads() -> int:
    # Half the cores: the learner is usually watching a video meanwhile.
    return max(1, min(8, (os.cpu_count() or 2) // 2))
