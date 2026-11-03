"""Test doubles for speech recognition: a tiny catalog whose "downloads" are
generated locally, fake hardware, and a scripted recognizer."""

from __future__ import annotations

import hashlib
import io
import threading
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from videotutor.app.errors import Cancelled, DownloadError
from videotutor.app.ports import NvidiaGpu, RecognitionRequest
from videotutor.domain.speech import Component, ComponentKind, RemoteFile, Word


def payload(name: str) -> bytes:
    return f"content of {name}\n".encode() * 50


def zip_payload(name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        # A fixed timestamp: the archive's sha256 must not change between calls.
        for member, data in [
            ("pkg/bin/lib_a.dll", b"A" * 100),
            ("pkg/bin/lib_b.dll", b"B" * 100),
            ("pkg/readme.txt", b"not wanted"),
        ]:
            z.writestr(zipfile.ZipInfo(member, date_time=(2020, 1, 1, 0, 0, 0)), data)
    return buf.getvalue()


def remote(name: str, *, archive: bool = False) -> RemoteFile:
    data = zip_payload(name) if archive else payload(name)
    return RemoteFile(
        name,
        len(data),
        hashlib.sha256(data).hexdigest(),
        (f"https://primary.example/{name}", f"https://mirror.example/{name}"),
        ("pkg/bin/*.dll",) if archive else (),
    )


def model(cid: str, *, prefers_gpu: bool = False) -> Component:
    return Component(cid, ComponentKind.SPEECH_MODEL, cid, "", (remote(f"{cid}.bin"),), prefers_gpu)


BIG = model("model-big", prefers_gpu=True)
SMALL = model("model-small")
RUNTIME = Component(
    "gpu-rt", ComponentKind.GPU_RUNTIME, "GPU", "", (remote("rt.whl", archive=True),)
)
MODELS = (BIG, SMALL)


class FakeDownloader:
    """Serves the payload a RemoteFile describes; audio gets fixed bytes."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path, Mapping[str, str]]] = []
        self.fail_names: set[str] = set()
        self.gate: threading.Event | None = None  # set: block until released or stopped

    def download(
        self,
        urls: Sequence[str],
        dest: Path,
        *,
        size: int | None,
        sha256: str | None,
        headers: Mapping[str, str],
        progress: Callable[[int], None],
        stop: threading.Event,
    ) -> None:
        self.calls.append((list(urls), dest, dict(headers)))
        if self.gate is not None:
            while not self.gate.wait(0.02):
                if stop.is_set():
                    raise Cancelled(dest.name)
        if dest.name in self.fail_names:
            raise DownloadError("boom")
        if dest.suffix == ".whl":
            data = zip_payload(dest.name)
        elif sha256 is None:
            data = b"audio" * 100
        else:
            data = payload(dest.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        progress(len(data))


class FakeHardware:
    def __init__(self, gpu: NvidiaGpu | None = None) -> None:
        self.gpu = gpu

    def nvidia_gpu(self) -> NvidiaGpu | None:
        return self.gpu


GOOD_GPU = NvidiaGpu("RTX 4060", 8192, "560.94")


class FakeRecognizer:
    """Answers with scripted words; can fail on the GPU or block until stopped."""

    def __init__(self, words: list[Word] | None = None) -> None:
        self.words = (
            words
            if words is not None
            else [
                Word(0, 400, " Hello"),
                Word(400, 900, " world."),
                Word(2000, 2500, " Again."),
            ]
        )
        self.requests: list[RecognitionRequest] = []
        self.gpu_broken = False
        self.block = False

    def recognize(
        self,
        request: RecognitionRequest,
        progress: Callable[[int, int], None],
        stop: threading.Event,
    ) -> list[Word]:
        from videotutor.app.errors import GpuUnavailableError

        self.requests.append(request)
        if request.device == "cuda" and self.gpu_broken:
            raise GpuUnavailableError("no cuDNN")
        while self.block:
            if stop.wait(0.02):
                raise Cancelled("recognition")
        progress(1000, 2500)
        return self.words
