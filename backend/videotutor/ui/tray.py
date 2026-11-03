"""System tray icon: the app's only always-visible presence.

Deliberately dumb: every menu item calls an action supplied by bootstrap.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pystray
from PIL import Image

log = logging.getLogger("tray")


@dataclass(frozen=True)
class TrayActions:
    open_home: Callable[[], None]
    open_extension_folder: Callable[[], None]
    open_logs: Callable[[], None]
    quit: Callable[[], None]


class Tray:
    def __init__(self, title: str, version: str, icon_file: Path, actions: TrayActions) -> None:
        def call(action: Callable[[], None]) -> Callable[..., None]:
            def handler(*_: Any) -> None:
                try:
                    action()
                except Exception:
                    log.exception("tray action failed")

            return handler

        menu = pystray.Menu(
            pystray.MenuItem(f"打开 {title}", call(actions.open_home), default=True),
            pystray.MenuItem("打开扩展所在文件夹", call(actions.open_extension_folder)),
            pystray.MenuItem("查看日志", call(actions.open_logs)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"版本 {version}", None, enabled=False),
            pystray.MenuItem("退出", call(actions.quit)),
        )
        self._icon = pystray.Icon(title, Image.open(icon_file), title, menu)

    def run(self) -> None:
        self._icon.run()  # blocks until stop()

    def stop(self) -> None:
        self._icon.stop()
