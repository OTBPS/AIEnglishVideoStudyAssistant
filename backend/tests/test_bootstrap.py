from __future__ import annotations

import json
import socket
import threading
import urllib.request

import pytest

from videotutor.app.lifecycle import AppState
from videotutor.app.ports import Locations
from videotutor.bootstrap import Application, Options, smoke_test
from videotutor.core.product import Product
from videotutor.infra.single_instance import RuntimeFile

from .conftest import FakeDesktop, InMemorySecretStore

HEADLESS = Options(tray=False, open_browser=False)


def test_start_serve_stop(product: Product, locations: Locations, desktop: FakeDesktop) -> None:
    app = Application(product, locations, HEADLESS, desktop=desktop, secrets=InMemorySecretStore())
    app.start()
    try:
        assert app.lifecycle.state is AppState.RUNNING
        with urllib.request.urlopen(app.url + "api/health", timeout=5) as resp:
            assert json.load(resp)["version"] == product.version
        assert RuntimeFile(locations.runtime_file).read() is not None
        app.activate()
        assert desktop.opened_urls == [app.url]
    finally:
        assert app.stop() == []
    assert app.lifecycle.state is AppState.STOPPED
    assert not locations.runtime_file.exists()


def test_run_opens_home_and_returns_on_quit(
    product: Product, locations: Locations, desktop: FakeDesktop
) -> None:
    app = Application(
        product,
        locations,
        Options(tray=False, open_browser=True),
        desktop=desktop,
        secrets=InMemorySecretStore(),
    )
    result: list[int] = []
    t = threading.Thread(target=lambda: result.append(app.run()))
    t.start()
    for _ in range(200):
        if desktop.opened_urls:
            break
        threading.Event().wait(0.02)
    app.request_quit()
    t.join(timeout=5)
    assert result == [0]
    assert len(desktop.opened_urls) == 1


def test_startup_failure_rolls_back(
    product: Product, locations: Locations, desktop: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_ports(*_: object) -> socket.socket:
        raise OSError("all ports taken")

    monkeypatch.setattr("videotutor.bootstrap.bind_first_free", no_ports)
    app = Application(product, locations, HEADLESS, desktop=desktop, secrets=InMemorySecretStore())
    with pytest.raises(OSError, match="all ports taken"):
        app.start()
    assert app.lifecycle.state is AppState.STOPPED
    with pytest.raises(RuntimeError, match="not started"):
        _ = app.url


def test_smoke_test_passes(product: Product, locations: Locations) -> None:
    assert smoke_test(product, locations) == 0
