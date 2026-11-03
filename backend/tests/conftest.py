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


class InMemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class FakeLanguageModel:
    """Returns queued answers (or raises queued exceptions) and records prompts."""

    def __init__(self, *answers: object, model_name: str = "fake-model") -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, object]] = []
        self._name = model_name

    @property
    def model_name(self) -> str:
        return self._name

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, object], max_tokens: int
    ) -> dict[str, object]:
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        assert isinstance(answer, dict)
        return answer


class FixedModelProvider:
    def __init__(self, model: FakeLanguageModel | None) -> None:
        self.model = model

    def current(self) -> FakeLanguageModel | None:
        return self.model
