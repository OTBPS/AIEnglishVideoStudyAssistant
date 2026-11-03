"""Single-instance guard and the runtime file that lets a second launch
find the first.

A tray app that is already running looks exactly like one that failed to
launch: you double-click, nothing on screen changes. So a second launch does
not start a second copy; it asks the running one to open its page, and exits.

Exclusivity comes from a named mutex (ported from the sister project's
`app/single_instance.py`): CreateMutexW is atomic and reports
ERROR_ALREADY_EXISTS, and the kernel releases it when the process dies, so a
crash can never leave behind something that blocks every future launch. The
installer uses the same mutex name (AppMutex) to detect a running copy.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

from .atomic import write_text_atomic

log = logging.getLogger("instance")

_ERROR_ALREADY_EXISTS = 183


class InstanceMutex:
    def __init__(self, name: str) -> None:
        self.name = name
        self._handle: Any = None

    def claim(self) -> bool:
        """True if we are the only instance. Never blocks startup on failure."""
        if sys.platform != "win32":
            return True
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            return True  # cannot tell; do not block
        first = bool(kernel32.GetLastError() != _ERROR_ALREADY_EXISTS)
        if first:
            self._handle = handle
        else:
            kernel32.CloseHandle(handle)
        return first

    def release(self) -> None:
        if self._handle is None or sys.platform != "win32":
            return
        import ctypes

        ctypes.windll.kernel32.CloseHandle(self._handle)
        self._handle = None


class RuntimeFile:
    """{"port", "pid"} of the running instance."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, port: int) -> None:
        write_text_atomic(self.path, json.dumps({"port": port, "pid": os.getpid()}))

    def read(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def remove_if_ours(self) -> None:
        data = self.read()
        if data is not None and data.get("pid") == os.getpid():
            with contextlib.suppress(OSError):
                self.path.unlink()

    def notify_existing(self, timeout: float = 2.0) -> bool:
        """Ask the running instance to open its page. True if it answered."""
        data = self.read()
        port = data.get("port") if data else None
        if not isinstance(port, int):
            return False
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/activate", method="POST", data=b""
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 -- fixed localhost URL
                return bool(resp.status == 200)
        except OSError:
            return False
