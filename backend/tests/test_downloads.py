"""HttpDownloader against a real local HTTP server."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from videotutor.app.errors import Cancelled, DownloadError
from videotutor.infra import downloads
from videotutor.infra.downloads import HttpDownloader

DATA = bytes(range(256)) * 4096  # 1 MiB
SHA = hashlib.sha256(DATA).hexdigest()


class Server:
    def __init__(self) -> None:
        self.ranges = True  # honour Range requests
        self.cut_after: int | None = None  # drop the connection after this many bytes, once
        self.requests: list[tuple[str, str | None, str | None]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                server.requests.append(
                    (self.path, self.headers.get("Range"), self.headers.get("Referer"))
                )
                if self.path.startswith("/missing"):
                    self.send_error(404)
                    return
                start = 0
                rng = self.headers.get("Range")
                if rng and server.ranges:
                    start = int(rng.removeprefix("bytes=").rstrip("-"))
                    if start >= len(DATA):
                        self.send_response(416)
                        self.end_headers()
                        return
                    self.send_response(206)
                else:
                    self.send_response(200)
                body = DATA[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if server.cut_after is not None:
                    cut, server.cut_after = server.cut_after, None
                    self.wfile.write(body[:cut])
                    self.wfile.flush()
                    self.connection.close()
                    return
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def server() -> Iterator[Server]:
    s = Server()
    yield s
    s.httpd.shutdown()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloads, "RETRY_PAUSE_S", 0.01)
    monkeypatch.setattr(downloads, "CHUNK", 64 * 1024)


def _get(
    server: Server,
    dest: Path,
    *paths: str,
    sha: str | None = SHA,
    stop: threading.Event | None = None,
) -> list[int]:
    seen: list[int] = []
    HttpDownloader("test").download(
        [server.url + p for p in paths],
        dest,
        size=len(DATA) if sha else None,
        sha256=sha,
        headers={"Referer": "https://ref/"},
        progress=seen.append,
        stop=stop or threading.Event(),
    )
    return seen


def test_download_verifies_and_renames(server: Server, tmp_path: Path) -> None:
    dest = tmp_path / "f.bin"
    seen = _get(server, dest, "/f")
    assert dest.read_bytes() == DATA and seen[-1] == len(DATA)
    assert not (tmp_path / "f.bin.part").exists()
    assert server.requests[0][2] == "https://ref/"


def test_falls_back_to_the_mirror(server: Server, tmp_path: Path) -> None:
    _get(server, tmp_path / "f.bin", "/missing", "/f")
    assert [r[0] for r in server.requests] == ["/missing", "/f"]


def test_resumes_after_a_dropped_connection(server: Server, tmp_path: Path) -> None:
    server.cut_after = 300_000
    _get(server, tmp_path / "f.bin", "/f")
    assert (tmp_path / "f.bin").read_bytes() == DATA
    assert server.requests[-1][1] == "bytes=300000-"


def test_restarts_when_the_server_ignores_range(server: Server, tmp_path: Path) -> None:
    (tmp_path / "f.bin.part").write_bytes(DATA[:1000])
    server.ranges = False
    _get(server, tmp_path / "f.bin", "/f")
    assert (tmp_path / "f.bin").read_bytes() == DATA


def test_complete_part_file_is_just_verified(server: Server, tmp_path: Path) -> None:
    (tmp_path / "f.bin.part").write_bytes(DATA)
    _get(server, tmp_path / "f.bin", "/f")
    assert server.requests == []  # nothing left to fetch
    (tmp_path / "g.bin.part").write_bytes(DATA)
    _get(server, tmp_path / "g.bin", "/g", sha=None)  # unknown size: the server says 416
    assert (tmp_path / "g.bin").read_bytes() == DATA


def test_checksum_mismatch_deletes_the_file(server: Server, tmp_path: Path) -> None:
    with pytest.raises(DownloadError) as exc:
        _get(server, tmp_path / "f.bin", "/f", sha="1" * 64)
    assert "校验失败" in exc.value.user_message
    assert not (tmp_path / "f.bin").exists() and not (tmp_path / "f.bin.part").exists()


def test_every_mirror_failing_is_a_download_error(server: Server, tmp_path: Path) -> None:
    with pytest.raises(DownloadError, match="404"):
        _get(server, tmp_path / "f.bin", "/missing-a", "/missing-b")
    assert len(server.requests) == 4  # each URL twice
    with pytest.raises(DownloadError):
        HttpDownloader("t").download(
            [],
            tmp_path / "x",
            size=None,
            sha256=None,
            headers={},
            progress=print,
            stop=threading.Event(),
        )


def test_stop_cancels(server: Server, tmp_path: Path) -> None:
    stop = threading.Event()
    stop.set()
    with pytest.raises(Cancelled):
        _get(server, tmp_path / "f.bin", "/f", stop=stop)
