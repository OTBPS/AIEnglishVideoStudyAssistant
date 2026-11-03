"""What the machine can run: is there an NVIDIA GPU, and which one?

nvidia-smi ships with every NVIDIA driver (in System32), so it answers
without any CUDA installation. Whether CUDA actually works is only known by
trying -- the recognizer does that and falls back to the CPU.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from videotutor.app.ports import NvidiaGpu

log = logging.getLogger("hardware")

TIMEOUT_S = 8.0
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SystemHardware:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._probed = False
        self._gpu: NvidiaGpu | None = None

    def nvidia_gpu(self) -> NvidiaGpu | None:
        with self._lock:  # hardware does not change while the app runs: ask once
            if not self._probed:
                self._gpu = _query_nvidia_smi()
                self._probed = True
                log.info("NVIDIA GPU: %s", self._gpu or "none")
            return self._gpu


def _nvidia_smi() -> str | None:
    system32 = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
    return str(system32) if system32.exists() else shutil.which("nvidia-smi")


def _query_nvidia_smi() -> NvidiaGpu | None:
    exe = _nvidia_smi()
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=True,
            creationflags=NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("nvidia-smi failed: %s", exc)
        return None
    return parse_nvidia_smi(out)


def parse_nvidia_smi(out: str) -> NvidiaGpu | None:
    """The GPU with the most memory, from `--format=csv,noheader,nounits` output."""
    gpus: list[NvidiaGpu] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[1].isdigit():
            gpus.append(NvidiaGpu(parts[0], int(parts[1]), parts[2]))
    return max(gpus, key=lambda g: g.memory_mb) if gpus else None
