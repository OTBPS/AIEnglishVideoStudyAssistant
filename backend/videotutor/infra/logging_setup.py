"""Logging, and making failures visible when there is no console.

The installed app is a windowed exe: `sys.stdout` and `sys.stderr` are None.
Any library that writes to stderr while being imported then raises on None
and the process dies before logging exists. From the desktop that looks like
"double-click does nothing", the single most expensive bug in the sister
project (`Ai Simultaneous interpretation/app/console.py`). Ported from there.

The log is size-capped (1 MB x 2): the install must not grow on its own.

Imports only the standard library: it runs before the heavy dependencies.
"""

from __future__ import annotations

import contextlib
import io
import logging
import logging.handlers
import sys
from pathlib import Path
from types import TracebackType
from typing import TextIO

LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 1
_FMT = "%(asctime)s %(levelname)-5s %(name)-10s %(message)s"


class StdStreams:
    """Remembers whether stdout/stderr were substituted, without module globals."""

    def __init__(self) -> None:
        self.substituted = False

    def ensure(self, log_dir: Path) -> None:
        """Point missing stdout/stderr at a file before anything else is imported."""
        if sys.stdout is not None and sys.stderr is not None:
            return
        stream: TextIO
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            stream = (log_dir / "stdio.txt").open(
                "a", encoding="utf-8", errors="replace", buffering=1
            )
        except OSError:
            stream = io.StringIO()  # swallow, never crash
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
        self.substituted = True

    def has_console(self) -> bool:
        """Can the user see what we write to stderr?

        NOT simply `sys.stderr is not None`: once ensure() has replaced the
        streams that check would report a console where there is none and
        suppress the error dialog exactly when it matters.
        """
        if self.substituted:
            return False
        return sys.stdout is not None and sys.stderr is not None


def setup_logging(log_file: Path, *, console: bool, level: str = "info") -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError):
                reconfigure(encoding="utf-8", errors="replace")

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):  # idempotent re-setup
        root.removeHandler(h)
        h.close()

    if console:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FMT, "%H:%M:%S"))
        root.addHandler(handler)

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
        fh.setFormatter(logging.Formatter(_FMT))
        root.addHandler(fh)
    except OSError:
        pass  # never block startup on a log

    for noisy in ("uvicorn.access", "PIL", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def install_crash_hook(show: ErrorDialog, log_file: Path) -> None:
    """Unhandled exceptions: always logged, and shown to the user via `show`."""
    log = logging.getLogger("crash")

    def hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("unhandled exception", exc_info=(exc_type, exc, tb))
        show(f"{exc_type.__name__}: {exc}\n\n详细日志：\n{log_file}")

    sys.excepthook = hook


class ErrorDialog:
    """Shows an error to a user who has no console; no-op when they do."""

    def __init__(self, streams: StdStreams, title: str) -> None:
        self._streams = streams
        self._title = title

    def __call__(self, text: str) -> None:
        if self._streams.has_console():
            print(text, file=sys.stderr)
        else:
            message_box(text, self._title, error=True)


def message_box(text: str, title: str, *, error: bool = False) -> None:
    """Native Windows message box; silently does nothing elsewhere."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
    except Exception:  # noqa: BLE001, S110 -- a failing dialog must never mask the real error
        pass
