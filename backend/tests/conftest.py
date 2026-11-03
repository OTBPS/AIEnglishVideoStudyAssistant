from __future__ import annotations

from pathlib import Path

import pytest

from videotutor.app.ports import Locations
from videotutor.core.product import Product, load_product
from videotutor.infra import paths

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def product() -> Product:
    return load_product(REPO / "product.toml")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv(paths.HOME_ENV, str(h))
    return h


@pytest.fixture
def locations(home: Path, product: Product) -> Locations:
    loc = paths.resolve(product.name)
    paths.ensure_dirs(loc)
    return loc


class FakeDesktop:
    def __init__(self) -> None:
        self.opened_paths: list[Path] = []
        self.opened_urls: list[str] = []
        self.messages: list[tuple[str, str, bool]] = []

    def open_path(self, path: Path) -> None:
        self.opened_paths.append(path)

    def open_url(self, url: str) -> None:
        self.opened_urls.append(url)

    def message(self, text: str, title: str, *, error: bool = False) -> None:
        self.messages.append((text, title, error))


@pytest.fixture
def desktop() -> FakeDesktop:
    return FakeDesktop()
