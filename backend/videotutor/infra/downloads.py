"""HTTP downloads that survive bad networks: resume, mirrors, integrity check.

* A partial download is kept as `<name>.part` and resumed with a Range
  request -- also across mirrors and app restarts, since every mirror serves
  identical bytes.
* Each URL is tried in turn (twice round the list); a stalled connection
  times out and moves on rather than hanging.
* With a sha256 the file is verified before it gets its final name; a
  mismatch deletes it, so a corrupt file can never be mistaken for a good one.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from videotutor.app.errors import Cancelled, DiskFullError, DownloadError

log = logging.getLogger("downloads")

CHUNK = 1 << 20
TIMEOUT_S = 20.0  # connect, and each read: a stalled mirror gives way to the next
ROUNDS = 2
RETRY_PAUSE_S = 2.0
PROGRESS_EVERY = 4 * CHUNK


class _Restart(Exception):
    """The server ignored our Range request: start the file over."""


class HttpDownloader:
    def __init__(self, user_agent: str) -> None:
        self._user_agent = user_agent

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
        if not urls:
            raise DownloadError("no URL to download from")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        if size is not None and part.exists() and part.stat().st_size > size:
            part.unlink()
        last_error = ""
        for attempt in range(ROUNDS * len(urls)):
            if stop.is_set():
                raise Cancelled(dest.name)
            url = urls[attempt % len(urls)]
            try:
                self._fetch(url, part, size, headers, progress, stop)
                break
            except _Restart:
                part.unlink(missing_ok=True)
                last_error = f"{_host(url)} ignored the range request"
            except (OSError, http.client.HTTPException) as exc:
                if isinstance(exc, OSError) and _disk_full(exc):
                    raise DiskFullError(str(exc)) from exc
                last_error = f"{_host(url)}: {type(exc).__name__}: {exc}"
                log.warning(
                    "download of %s failed (attempt %d): %s", dest.name, attempt + 1, last_error
                )
            stop.wait(RETRY_PAUSE_S)
        else:
            raise DownloadError(f"{dest.name}: {last_error}")
        self._verify(part, size, sha256, stop)
        os.replace(part, dest)

    # ------------------------------------------------------------ internals
    def _fetch(
        self,
        url: str,
        part: Path,
        size: int | None,
        headers: Mapping[str, str],
        progress: Callable[[int], None],
        stop: threading.Event,
    ) -> None:
        have = part.stat().st_size if part.exists() else 0
        if size is not None and have == size:
            return  # complete already, only the rename was missing
        if urllib.parse.urlsplit(url).scheme not in ("https", "http"):
            raise DownloadError(f"refusing to fetch {url!r}")
        req = urllib.request.Request(  # noqa: S310 -- scheme checked just above
            url, headers={"User-Agent": self._user_agent, **headers}
        )
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT_S)  # noqa: S310 -- https URLs only (RemoteFile / app checks)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and have:  # nothing past what we have: the file is complete
                return
            raise
        with resp:
            if have and resp.status != 206:
                raise _Restart
            expected = _content_length(resp)
            received = 0
            reported = have
            with part.open("ab") as f:
                while chunk := resp.read(CHUNK):
                    if stop.is_set():
                        raise Cancelled(part.name)
                    f.write(chunk)
                    received += len(chunk)
                    if have + received - reported >= PROGRESS_EVERY:
                        reported = have + received
                        progress(reported)
            progress(have + received)
            if expected is not None and received < expected:
                raise http.client.IncompleteRead(b"", expected - received)

    def _verify(
        self, part: Path, size: int | None, sha256: str | None, stop: threading.Event
    ) -> None:
        actual = part.stat().st_size
        if size is not None and actual != size:
            part.unlink(missing_ok=True)
            raise DownloadError(f"{part.name}: got {actual} bytes, expected {size}")
        if sha256 is None:
            return
        digest = hashlib.sha256()
        with part.open("rb") as f:
            while chunk := f.read(CHUNK):
                if stop.is_set():
                    raise Cancelled(part.name)
                digest.update(chunk)
        if digest.hexdigest() != sha256:
            part.unlink(missing_ok=True)
            raise DownloadError(
                f"{part.name}: sha256 mismatch",
                user_message="下载的文件校验失败（网络传输出错），已删除，请重试。",
            )


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc


def _content_length(resp: http.client.HTTPResponse) -> int | None:
    value = resp.headers.get("Content-Length")
    with contextlib.suppress(ValueError):
        return int(value) if value is not None else None
    return None


def _disk_full(exc: OSError) -> bool:
    return exc.errno == 28 or getattr(exc, "winerror", None) in (39, 112)  # ENOSPC, ERROR_DISK_FULL
