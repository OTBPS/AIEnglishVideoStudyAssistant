"""Interfaces to the outside world. The app layer depends on these; adapters
in videotutor.infra implement them; bootstrap wires one to the other.

Add a port only when there is a real second implementation or a real need
to fake it in tests -- not speculatively.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Locations:
    """Every path the app uses. Built once by infra.paths, then passed around.

    install_dir is replaced on every upgrade; home survives upgrades and
    uninstalls (unless the user opts in to deleting it).
    """

    install_dir: Path
    home: Path
    data_dir: Path
    logs_dir: Path
    models_dir: Path
    extension_dir: Path
    web_dir: Path
    icon_file: Path
    settings_file: Path
    runtime_file: Path


class Desktop(Protocol):
    """Shell integration: what a user would do with Explorer or a browser."""

    def open_path(self, path: Path) -> None: ...

    def open_url(self, url: str) -> None: ...

    def message(self, text: str, title: str, *, error: bool = False) -> None: ...
