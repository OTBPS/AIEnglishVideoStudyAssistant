"""Windows shell integration (implements app.ports.Desktop)."""

from __future__ import annotations

import logging
import os
import sys
import webbrowser
from pathlib import Path

from .logging_setup import message_box

log = logging.getLogger("desktop")


class WindowsDesktop:
    def open_path(self, path: Path) -> None:
        if sys.platform != "win32":
            log.info("open_path(%s) ignored: not Windows", path)
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined,unused-ignore]  # noqa: S606
        except OSError as exc:
            log.warning("cannot open %s: %s", path, exc)

    def open_url(self, url: str) -> None:
        if not webbrowser.open(url):
            log.warning("no browser could open %s", url)

    def message(self, text: str, title: str, *, error: bool = False) -> None:
        message_box(text, title, error=error)
