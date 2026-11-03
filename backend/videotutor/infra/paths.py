"""Filesystem layout, for both a source checkout and the installed exe.

Nothing here hard-codes a drive letter. The sister project pinned its models
to D:\\AI\\Models, which is exactly what made it impossible to hand to anyone
else.

Imports only the standard library: it is used before logging exists.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from videotutor.app.ports import Locations

HOME_ENV = "VIDEOTUTOR_HOME"  # override for tests and portable runs


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """Folder holding VideoTutor.exe (frozen) or the repository root (source)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def bundle_dir() -> Path:
    """Where read-only bundled data lives: _internal/ (frozen) or the repo."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", install_dir()))
    return install_dir()


def product_file() -> Path:
    return bundle_dir() / "product.toml"


def package_dir() -> Path:
    if is_frozen():
        return bundle_dir() / "videotutor"
    return Path(__file__).resolve().parents[1]


def home_dir(app_name: str) -> Path:
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    # Must match the uninstaller's "delete my data" path in installer/videotutor.iss.
    return Path(base) / app_name


def worker_command() -> tuple[list[str], Path | None]:
    """How to start this program as the speech-recognition worker, and where."""
    if is_frozen():
        return [sys.executable, "--asr-worker"], None
    # python.exe, not pythonw: the worker talks over stdout. Run from backend/
    # so `-m videotutor` resolves to this checkout.
    return [sys.executable, "-m", "videotutor", "--asr-worker"], package_dir().parent


def per_home_name(base: str, home: Path) -> str:
    """Name a per-installation resource (instance mutex, credential-store service).

    One instance per data directory, not per machine.

    The normal install uses the plain name (the installer's AppMutex checks
    it). A run with VIDEOTUTOR_HOME pointing elsewhere (tests, demos, a
    portable copy) gets its own name, so it neither blocks nor is blocked by
    the real app -- two processes on *different* data cannot conflict.
    """
    if not os.environ.get(HOME_ENV):
        return base
    digest = hashlib.sha256(str(home.resolve()).lower().encode()).hexdigest()[:12]
    return f"{base}-{digest}"


def resolve(app_name: str) -> Locations:
    home = home_dir(app_name)
    pkg = package_dir()
    return Locations(
        install_dir=install_dir(),
        home=home,
        data_dir=home / "data",
        logs_dir=home / "logs",
        models_dir=home / "models",
        extension_dir=install_dir() / "extension",
        web_dir=pkg / "ui" / "web",
        icon_file=pkg / "assets" / "app.ico",
        settings_file=home / "settings.json",
        runtime_file=home / "runtime.json",
        database_file=home / "data" / "tutor.db",
        lexicon_file=pkg / "assets" / "lexicon_en.tsv.gz",
        cache_dir=home / "cache",
    )


def ensure_dirs(loc: Locations) -> None:
    for d in (loc.data_dir, loc.logs_dir, loc.models_dir, loc.cache_dir):
        d.mkdir(parents=True, exist_ok=True)
