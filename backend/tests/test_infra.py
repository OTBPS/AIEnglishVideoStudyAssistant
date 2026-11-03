from __future__ import annotations

import json
import os
import socket
import sys
import urllib.request
from pathlib import Path

import pytest
from fastapi import FastAPI

from videotutor.core.product import Product
from videotutor.infra import paths
from videotutor.infra.atomic import write_text_atomic
from videotutor.infra.desktop import WindowsDesktop
from videotutor.infra.http_server import HttpServer, NoFreePortError, bind_first_free
from videotutor.infra.logging_setup import ErrorDialog, StdStreams, setup_logging
from videotutor.infra.single_instance import InstanceMutex, RuntimeFile


# ---------------------------------------------------------------- paths
def test_locations_live_under_home(home: Path, product: Product) -> None:
    loc = paths.resolve(product.name)
    assert loc.home == home
    for p in (loc.data_dir, loc.logs_dir, loc.models_dir, loc.settings_file):
        assert home in p.parents
    assert loc.extension_dir == loc.install_dir / "extension"
    assert (loc.web_dir / "index.html").exists()
    assert loc.icon_file.exists()
    assert paths.product_file().exists()
    assert not paths.is_frozen()


def test_home_defaults_to_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(paths.HOME_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.home_dir("VideoTutor") == tmp_path / "VideoTutor"


def test_frozen_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = tmp_path / "VideoTutor" / "VideoTutor.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(exe.parent / "_internal"), raising=False)
    assert paths.install_dir() == exe.parent
    assert paths.product_file() == exe.parent / "_internal" / "product.toml"
    assert paths.package_dir() == exe.parent / "_internal" / "videotutor"


# ---------------------------------------------------------------- atomic
def test_atomic_write_replaces_whole_file(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "f.json"
    write_text_atomic(target, "one")
    write_text_atomic(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert [p.name for p in target.parent.iterdir()] == ["f.json"]


# ---------------------------------------------------------------- http
def test_bind_skips_busy_ports() -> None:
    first = bind_first_free(range(20000, 20100))
    port = first.getsockname()[1]
    try:
        second = bind_first_free([port, port + 1])
        assert second.getsockname()[1] == port + 1
        second.close()
        with pytest.raises(NoFreePortError):
            bind_first_free([port])
    finally:
        first.close()


def test_http_server_serves_and_stops() -> None:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"pong": "yes"}

    server = HttpServer(app, bind_first_free(range(20100, 20200)))
    server.start()
    try:
        with urllib.request.urlopen(server.url + "ping", timeout=5) as resp:
            assert json.load(resp) == {"pong": "yes"}
    finally:
        server.stop()
    with socket.socket() as s:
        s.bind(("127.0.0.1", server.port))  # released after stop


# ---------------------------------------------------------------- single instance
@pytest.mark.skipif(sys.platform != "win32", reason="named mutex is Windows-only")
def test_mutex_is_exclusive_and_released() -> None:
    name = f"Local\\videotutor-test-{os.getpid()}"
    a, b = InstanceMutex(name), InstanceMutex(name)
    assert a.claim()
    assert not b.claim()
    a.release()
    assert b.claim()
    b.release()


def test_runtime_file_roundtrip(tmp_path: Path) -> None:
    rt = RuntimeFile(tmp_path / "runtime.json")
    assert rt.read() is None
    assert not rt.notify_existing()
    rt.write(8765)
    assert rt.read() == {"port": 8765, "pid": os.getpid()}
    rt.remove_if_ours()
    assert not rt.path.exists()


def test_runtime_file_of_another_process_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"port": 1, "pid": -1}), encoding="utf-8")
    RuntimeFile(path).remove_if_ours()
    assert path.exists()
    path.write_text("[]", encoding="utf-8")
    assert RuntimeFile(path).read() is None


def test_notify_existing_reaches_a_live_server(tmp_path: Path) -> None:
    hits: list[int] = []
    app = FastAPI()

    @app.post("/api/activate")
    def activate() -> dict[str, bool]:
        hits.append(1)
        return {"ok": True}

    server = HttpServer(app, bind_first_free(range(20200, 20300)))
    server.start()
    try:
        rt = RuntimeFile(tmp_path / "runtime.json")
        rt.write(server.port)
        assert rt.notify_existing()
        assert hits == [1]
    finally:
        server.stop()
    assert not rt.notify_existing(timeout=0.5)


# ---------------------------------------------------------------- logging
def test_std_streams_substitution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "stderr", None)
    streams = StdStreams()
    streams.ensure(tmp_path)
    assert sys.stderr is not None
    assert streams.substituted and not streams.has_console()
    print("routed", file=sys.stderr)
    sys.stderr.flush()
    assert "routed" in (tmp_path / "stdio.txt").read_text(encoding="utf-8")


def test_error_dialog_prints_when_there_is_a_console(capsys: pytest.CaptureFixture[str]) -> None:
    ErrorDialog(StdStreams(), "t")("something broke")
    assert "something broke" in capsys.readouterr().err


def test_setup_logging_writes_file(tmp_path: Path) -> None:
    import logging

    log_file = tmp_path / "logs" / "app.log"
    setup_logging(log_file, console=False)
    logging.getLogger("x").info("hello file")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "hello file" in log_file.read_text(encoding="utf-8")
    setup_logging(log_file, console=True)  # idempotent re-setup closes old handlers


def test_desktop_open_url_uses_webbrowser(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    WindowsDesktop().open_url("http://127.0.0.1:1/")
    assert opened == ["http://127.0.0.1:1/"]
