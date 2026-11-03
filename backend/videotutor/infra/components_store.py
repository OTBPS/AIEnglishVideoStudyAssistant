"""Downloaded components on disk: %LOCALAPPDATA%\\VideoTutor\\models\\<id>\\.

A component folder appears only once every file is downloaded, verified and
unpacked (staged in .partial\\<id>\\, then renamed), so a half-finished
install is never mistaken for a working one. The staging folder is kept
between attempts: an interrupted download resumes where it stopped.

installed.json records exactly which files (by sha256) the folder holds. If
the catalog pins different files under the same id, the folder no longer
counts as installed.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import logging
import os
import shutil
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from videotutor.app.errors import Cancelled, DiskFullError, DownloadError
from videotutor.app.ports import Downloader
from videotutor.domain.speech import Component, RemoteFile

from .atomic import write_text_atomic

log = logging.getLogger("components")

MARKER = "installed.json"
STAGING = ".partial"
TRASH = ".trash"


class FileComponentStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def directory(self, component: Component) -> Path:
        return self._root / component.id

    def is_installed(self, component: Component) -> bool:
        try:
            marker = json.loads((self.directory(component) / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(marker, dict) and marker.get("files") == _fingerprint(component)

    def free_bytes(self) -> int:
        self._root.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(self._root).free

    def install(
        self,
        component: Component,
        downloader: Downloader,
        progress: Callable[[int], None],
        stop: threading.Event,
    ) -> None:
        staging = self._root / STAGING / component.id
        out = staging / "out"
        out.mkdir(parents=True, exist_ok=True)
        done = 0
        for f in component.files:
            if stop.is_set():
                raise Cancelled(component.id)

            def report(n: int, base: int = done) -> None:
                progress(base + n)

            if f.extract:
                self._fetch_archive(f, staging, out, downloader, report, stop)
            elif _have(out / f.name, f.size):
                pass  # verified by an earlier attempt: a file only gets its name after that
            else:
                downloader.download(
                    f.urls,
                    out / f.name,
                    size=f.size,
                    sha256=f.sha256,
                    headers={},
                    progress=report,
                    stop=stop,
                )
            done += f.size
            progress(done)
        write_text_atomic(
            out / MARKER, json.dumps({"id": component.id, "files": _fingerprint(component)})
        )
        self._swap_in(component, out)
        shutil.rmtree(staging, ignore_errors=True)
        log.info("installed %s (%d bytes downloaded)", component.id, component.download_bytes)

    def remove(self, component: Component) -> None:
        for path in (self.directory(component), self._root / STAGING / component.id):
            if path.exists():
                self._discard(path)

    # ------------------------------------------------------------ internals
    def _fetch_archive(
        self,
        f: RemoteFile,
        staging: Path,
        out: Path,
        downloader: Downloader,
        progress: Callable[[int], None],
        stop: threading.Event,
    ) -> None:
        unpacked = staging / f"{f.name}.unpacked"
        if unpacked.exists():
            progress(f.size)
            return  # an earlier attempt got this far
        archive = staging / f.name
        downloader.download(
            f.urls, archive, size=f.size, sha256=f.sha256, headers={}, progress=progress, stop=stop
        )
        try:
            _extract(archive, f.extract, out, stop)
        except OSError as exc:
            if exc.errno == 28:
                raise DiskFullError(str(exc)) from exc
            raise
        unpacked.touch()
        archive.unlink(missing_ok=True)  # it was only a container: free the space now

    def _swap_in(self, component: Component, out: Path) -> None:
        final = self.directory(component)
        if final.exists():
            self._discard(final)
        os.replace(out, final)

    def _discard(self, path: Path) -> None:
        """Delete a folder. A DLL still loaded by a running worker cannot be
        deleted on Windows: move it aside and try again next time."""
        trash = self._root / TRASH
        trash.mkdir(parents=True, exist_ok=True)
        for old in trash.iterdir():
            shutil.rmtree(old, ignore_errors=True)
        target = trash / f"{path.name}-{os.getpid()}-{threading.get_ident()}"
        try:
            os.replace(path, target)
        except OSError:
            target = path
        shutil.rmtree(target, ignore_errors=True)


def _have(path: Path, size: int) -> bool:
    try:
        return path.stat().st_size == size
    except OSError:
        return False


def _fingerprint(component: Component) -> list[list[str]]:
    return [[f.name, f.sha256] for f in component.files]


def _extract(archive: Path, patterns: tuple[str, ...], out: Path, stop: threading.Event) -> None:
    """Copy the matching members, flattened into `out` (wheels keep DLLs in a
    bin/ folder; the worker wants them in one directory)."""
    found = 0
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if info.is_dir() or not any(fnmatch.fnmatchcase(info.filename, p) for p in patterns):
                continue
            if stop.is_set():
                raise Cancelled(archive.name)
            name = PurePosixPath(info.filename).name
            tmp = out / f"{name}.tmp"
            with z.open(info) as src, tmp.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            os.replace(tmp, out / name)
            found += 1
    if not found:
        with contextlib.suppress(OSError):
            archive.unlink()
        raise DownloadError(f"{archive.name}: none of {patterns} found in the archive")
