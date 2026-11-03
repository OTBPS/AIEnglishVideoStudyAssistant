from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from videotutor.app.ports import Locations
from videotutor.app.setup import SetupService
from videotutor.core.product import Product
from videotutor.infra.settings_store import JsonSettingsStore
from videotutor.ui.http.api import ApiDeps, create_app

from .conftest import FakeDesktop

PORT = 8765


@pytest.fixture
def activations() -> list[int]:
    return []


@pytest.fixture
def client(
    product: Product, locations: Locations, desktop: FakeDesktop, activations: list[int]
) -> Iterator[TestClient]:
    store = JsonSettingsStore(locations.settings_file)
    store.load()
    setup = SetupService(product, locations, desktop, store)
    app = create_app(
        ApiDeps(product, setup, lambda: activations.append(1), locations.web_dir), PORT
    )
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c


def test_health_reports_product(client: TestClient, product: Product) -> None:
    body = client.get("/api/health").json()
    assert body == {"app": product.name, "version": product.version, "protocol": product.protocol}


def test_setup_info_and_open_folder(
    client: TestClient, locations: Locations, desktop: FakeDesktop, product: Product
) -> None:
    info = client.get("/api/setup/info").json()
    assert info["extension_dir"] == str(locations.extension_dir)
    assert info["extension_id"] == product.extension_id
    assert info["first_run_done"] is False
    assert client.post("/api/setup/open-extension-folder").json() == {"ok": True}
    assert desktop.opened_paths == [locations.extension_dir]


def test_activate_without_origin_is_allowed(client: TestClient, activations: list[int]) -> None:
    assert client.post("/api/activate").status_code == 200
    assert activations == [1]


def test_home_page_is_served(client: TestClient) -> None:
    resp = client.get("/", follow_redirects=True)
    assert resp.status_code == 200
    assert "VideoTutor" in resp.text


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:9999", "null"])
def test_foreign_origin_cannot_change_state(
    client: TestClient, activations: list[int], origin: str
) -> None:
    resp = client.post("/api/activate", headers={"Origin": origin})
    assert resp.status_code == 403
    assert activations == []


def test_own_page_and_extension_origins_may_post(
    client: TestClient, product: Product, activations: list[int]
) -> None:
    for origin in (
        f"http://127.0.0.1:{PORT}",
        f"http://localhost:{PORT}",
        product.extension_origin,
    ):
        assert client.post("/api/activate", headers={"Origin": origin}).status_code == 200
    assert len(activations) == 3


def test_dns_rebinding_host_is_rejected(client: TestClient) -> None:
    resp = client.get("/api/health", headers={"Host": f"attacker.example:{PORT}"})
    assert resp.status_code == 403
