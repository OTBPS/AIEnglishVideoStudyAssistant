from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from videotutor.app.llm_settings import LlmSettingsService
from videotutor.app.ports import Locations
from videotutor.app.prewatch import PrewatchService
from videotutor.app.setup import SetupService
from videotutor.app.vocabulary import VocabularyService
from videotutor.core.product import Product
from videotutor.core.tasks import TaskSupervisor
from videotutor.infra.db.database import Database
from videotutor.infra.db.repositories import SqlitePrewatchRepository, SqliteWordStatusRepository
from videotutor.infra.settings_store import JsonSettingsStore
from videotutor.ui.http.api import ApiDeps, create_app

from .conftest import FakeDesktop, FakeLanguageModel, FixedModelProvider, InMemorySecretStore
from .test_videos import json3, make_service
from .test_vocab import LEXICON

PORT = 8765
VIDEO = "iG9CE55wbtY"
MANUAL = {"track_id": ".en", "language": "en", "kind": "manual", "name": "English"}


@pytest.fixture
def activations() -> list[int]:
    return []


@pytest.fixture
def database(locations: Locations) -> Iterator[Database]:
    db, _ = Database.open(locations.database_file)
    yield db
    db.close()


@pytest.fixture
def models() -> FixedModelProvider:
    return FixedModelProvider(None)


@pytest.fixture
def tasks() -> Iterator[TaskSupervisor]:
    t = TaskSupervisor(default_timeout=10)
    t.start()
    yield t
    t.shutdown(grace=2)


def build_deps(
    product: Product,
    locations: Locations,
    desktop: FakeDesktop,
    database: Database,
    tasks: TaskSupervisor,
    models: FixedModelProvider,
    on_activate: Callable[[], None] = lambda: None,
) -> ApiDeps:
    store = JsonSettingsStore(locations.settings_file)
    store.load()
    videos = make_service(database)
    vocabulary = VocabularyService(LEXICON, videos, SqliteWordStatusRepository(database), store)
    return ApiDeps(
        product=product,
        setup=SetupService(product, locations, desktop, store, lambda: database.read_only),
        videos=videos,
        vocabulary=vocabulary,
        prewatch=PrewatchService(
            videos, vocabulary, SqlitePrewatchRepository(database), models, tasks, store
        ),
        settings=store,
        llm_settings=LlmSettingsService(store, InMemorySecretStore(), models),
        tasks=tasks,
        on_activate=on_activate,
        web_dir=locations.web_dir,
    )


@pytest.fixture
def client(
    product: Product,
    locations: Locations,
    desktop: FakeDesktop,
    activations: list[int],
    database: Database,
    tasks: TaskSupervisor,
    models: FixedModelProvider,
) -> Iterator[TestClient]:
    deps = build_deps(
        product, locations, desktop, database, tasks, models, lambda: activations.append(1)
    )
    with TestClient(create_app(deps, PORT), base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c


def _open(client: TestClient, tracks: list[dict[str, str]] | None = None) -> dict[str, object]:
    resp = client.post(
        "/api/videos/open",
        json={
            "platform": "youtube",
            "external_id": VIDEO,
            "title": "A talk",
            "url": f"https://www.youtube.com/watch?v={VIDEO}",
            "duration_ms": 60000,
            "tracks": [MANUAL] if tracks is None else tracks,
        },
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


def test_video_flow_end_to_end(client: TestClient) -> None:
    opened = _open(client)
    assert opened["wanted_track"] == MANUAL and opened["transcript"] is None

    put = client.put(
        f"/api/videos/youtube/{VIDEO}/captions",
        json={"track": MANUAL, "format": "json3", "body": json3((0, 1500, "Hello  there"))},
    )
    assert put.json() == {
        "language": "en",
        "kind": "manual",
        "source": "youtube-json3",
        "segment_count": 1,
    }
    transcript = client.get(f"/api/videos/youtube/{VIDEO}/transcript").json()
    assert transcript["segments"] == [{"start_ms": 0, "end_ms": 1500, "text": "Hello there"}]
    assert _open(client)["wanted_track"] is None

    sid = opened["session_id"]
    session = client.post(
        f"/api/sessions/{sid}/events",
        json={"events": [{"type": "play", "position_ms": 0}, {"type": "pause", "position_ms": 0}]},
    ).json()
    assert session["state"] == "paused"
    assert client.post(f"/api/sessions/{sid}/close").json()["state"] == "closed"

    recent = client.get("/api/videos/recent").json()
    assert recent[0]["external_id"] == VIDEO and recent[0]["has_transcript"] is True


def test_video_errors_map_to_status_codes(client: TestClient) -> None:
    assert client.get(f"/api/videos/youtube/{VIDEO}/transcript").status_code == 404
    assert client.get("/api/videos/youtube/bad id!/transcript").status_code == 422
    assert client.get("/api/videos/vimeo/x/transcript").status_code == 422
    assert client.post("/api/sessions/12345/close").status_code == 404
    _open(client)
    bad = client.put(
        f"/api/videos/youtube/{VIDEO}/captions",
        json={"track": MANUAL, "format": "json3", "body": "not json"},
    )
    assert bad.status_code == 422 and bad.json()["error"] == "InvalidInputError"
    too_many = client.post("/api/sessions/1/events", json={"events": []})
    assert too_many.status_code == 422


def test_read_only_database_answers_409(
    product: Product, locations: Locations, desktop: FakeDesktop, tasks: TaskSupervisor
) -> None:
    from videotutor.infra.db.migrations import MIGRATIONS, Migration

    newer = (*MIGRATIONS, Migration(len(MIGRATIONS) + 1, "future", "CREATE TABLE f (x);"))
    Database.open(locations.database_file, newer)[0].close()
    db, report = Database.open(locations.database_file)
    assert report.read_only
    deps = build_deps(product, locations, desktop, db, tasks, FixedModelProvider(None))
    with TestClient(create_app(deps, PORT), base_url=f"http://127.0.0.1:{PORT}") as c:
        assert c.get("/api/setup/info").json()["database_read_only"] is True
        resp = c.post("/api/videos/open", json={"platform": "youtube", "external_id": VIDEO})
        assert resp.status_code == 409
    db.close()


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
    assert resp.headers["cache-control"] == "no-cache"  # upgrades never run stale scripts


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


# ---------------------------------------------------------------- vocabulary & pre-watch
def _with_transcript(client: TestClient) -> None:
    _open(client)
    lines = (
        (0, 3000, "Creativity is as important as literacy."),
        (3000, 3000, "As Robinson said, children take a chance."),
    )
    resp = client.put(
        f"/api/videos/youtube/{VIDEO}/captions",
        json={"track": MANUAL, "format": "json3", "body": json3(*lines)},
    )
    assert resp.status_code == 200


def test_vocabulary_spans_and_word_marks(client: TestClient) -> None:
    _with_transcript(client)
    v = client.get(f"/api/videos/youtube/{VIDEO}/vocabulary").json()
    lemmas = [w["lemma"] for w in v["words"]]
    assert "robinson" not in lemmas and v["level"] == 3000
    first_span = v["spans"][0][0]
    assert first_span[:2] == [0, 10] and lemmas[first_span[2]] == "creativity"
    assert -1 in [span[2] for span in v["spans"][1]]  # "Robinson": a name
    before = v["coverage"]

    assert client.put("/api/words/creativity", json={"status": "known"}).status_code == 200
    after = client.get(f"/api/videos/youtube/{VIDEO}/vocabulary").json()
    assert after["coverage"] > before
    assert client.put("/api/words/NotAWord", json={"status": "known"}).status_code == 422
    assert client.put("/api/words/creativity", json={"status": "bogus"}).status_code == 422


def test_prewatch_endpoints(client: TestClient, models: FixedModelProvider) -> None:
    _with_transcript(client)
    url = f"/api/videos/youtube/{VIDEO}/prewatch"
    glosses = f"/api/videos/youtube/{VIDEO}/glosses"
    assert client.get(url).json()["status"] == "not_configured"
    assert client.post(glosses, json={"lemma": "literacy"}).status_code == 409

    models.model = FakeLanguageModel(
        {
            "glosses": [
                {
                    "lemma": "literacy",
                    "part_of_speech": "n.",
                    "definition_en": "being able to read",
                    "definition_zh": "读写能力",
                }
            ]
        },
    )
    g = client.post(glosses, json={"lemma": "literacy"})
    assert g.status_code == 200 and g.json()["definition_zh"] == "读写能力"
    assert client.get(url).json()["status"] == "idle"
    assert client.post(glosses, json={"lemma": "Bad"}).status_code == 422


def test_settings_endpoints(client: TestClient, models: FixedModelProvider) -> None:
    assert client.get("/api/settings").json() == {"vocabulary_level": 3000, "auto_prewatch": True}
    changed = client.put("/api/settings", json={"vocabulary_level": 6000}).json()
    assert changed["vocabulary_level"] == 6000
    assert client.put("/api/settings", json={"vocabulary_level": 10}).status_code == 422

    llm = client.get("/api/settings/llm").json()
    assert llm == {
        "provider": "claude",
        "model": "claude-opus-5-5",
        "base_url": "",
        "has_key": False,
    }
    body = {"provider": "claude", "model": "claude-opus-5-5", "api_key": "sk-ant-x"}
    failed = client.put("/api/settings/llm", json=body).json()  # provider returns no model yet
    assert failed["ok"] is False and failed["config"]["has_key"] is True
    assert "sk-ant" not in str(failed)

    models.model = FakeLanguageModel({"ok": True})
    assert client.put("/api/settings/llm", json=body).json()["ok"] is True
    assert client.delete("/api/settings/llm/key").json()["has_key"] is False
    bad = {"provider": "openai_compatible", "model": "m", "base_url": "http://x"}
    assert client.put("/api/settings/llm", json=bad).status_code == 422
