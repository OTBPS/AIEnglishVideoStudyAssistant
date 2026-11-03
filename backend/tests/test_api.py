from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from videotutor.app.comprehension import ComprehensionService
from videotutor.app.exercises import ExerciseFactory
from videotutor.app.listening import ListeningService
from videotutor.app.llm_settings import LlmSettingsService
from videotutor.app.ports import Locations
from videotutor.app.prewatch import PrewatchService
from videotutor.app.review import ReviewService
from videotutor.app.setup import SetupService
from videotutor.app.speech import SpeechSetup
from videotutor.app.transcription import TranscriptionService
from videotutor.app.vocabulary import VocabularyService
from videotutor.core.product import Product
from videotutor.core.tasks import TaskSupervisor
from videotutor.infra.components_store import FileComponentStore
from videotutor.infra.db.database import Database
from videotutor.infra.db.repositories import (
    SqliteAttemptRepository,
    SqliteCardRepository,
    SqliteListeningIssueRepository,
    SqlitePrewatchRepository,
    SqliteWordStatusRepository,
)
from videotutor.infra.settings_store import JsonSettingsStore
from videotutor.infra.srs import FsrsScheduler
from videotutor.ui.http.api import ApiDeps, create_app

from .conftest import FakeDesktop, FakeLanguageModel, FixedModelProvider, InMemorySecretStore
from .speech_fakes import MODELS, RUNTIME, FakeDownloader, FakeHardware, FakeRecognizer
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
    prewatch_repo = SqlitePrewatchRepository(database)
    prewatch = PrewatchService(videos, vocabulary, prewatch_repo, models, tasks, store)
    attempts = SqliteAttemptRepository(database)
    review = ReviewService(
        cards=SqliteCardRepository(database),
        attempts=attempts,
        srs=FsrsScheduler(fuzz=False),
        exercises=ExerciseFactory(prewatch_repo, LEXICON, models),
        videos=videos,
        vocabulary=vocabulary,
        prefetch_gloss=prewatch.prefetch_gloss,
    )
    downloader = FakeDownloader()
    speech = SpeechSetup(
        MODELS, RUNTIME, FileComponentStore(locations.models_dir), downloader, FakeHardware(), tasks
    )
    transcription = TranscriptionService(
        videos, speech, downloader, FakeRecognizer(), tasks, locations.cache_dir
    )
    return ApiDeps(
        product=product,
        setup=SetupService(product, locations, desktop, store, lambda: database.read_only),
        videos=videos,
        vocabulary=vocabulary,
        prewatch=prewatch,
        review=review,
        comprehension=ComprehensionService(videos, prewatch_repo, attempts, models),
        listening=ListeningService(videos, SqliteListeningIssueRepository(database)),
        settings=store,
        llm_settings=LlmSettingsService(store, InMemorySecretStore(), models),
        speech=speech,
        transcription=transcription,
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


# ---------------------------------------------------------------- review
def test_review_endpoints(client: TestClient, models: FixedModelProvider) -> None:
    _with_transcript(client)
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
        }
    )
    assert (
        client.post(f"/api/videos/youtube/{VIDEO}/glosses", json={"lemma": "literacy"}).status_code
        == 200
    )
    collect = {"status": "learning", "platform": "youtube", "external_id": VIDEO}
    assert client.put("/api/words/literacy", json=collect).status_code == 200
    assert client.get("/api/review/summary").json() == {
        "due": 1,
        "total": 1,
        "reviewed_today": 0,
        "waiting_for_gloss": 0,
    }

    nxt = client.post(
        "/api/review/next",
        json={"context": "postwatch", "platform": "youtube", "external_id": VIDEO},
    ).json()["exercise"]
    # Only one meaning exists, so no distractors: a spelling exercise (word hidden).
    assert nxt["kind"] == "spell" and nxt["word"] is None and nxt["hint_zh"] == "读写能力"
    answer = "literacy"
    res = client.post("/api/review/answer", json={"exercise_id": nxt["id"], "answer": answer})
    assert res.status_code == 200 and res.json()["grade"] == "good"
    assert (
        client.post(
            "/api/review/answer", json={"exercise_id": nxt["id"], "answer": "x"}
        ).status_code
        == 404
    )
    done = client.post(
        "/api/review/next",
        json={
            "context": "postwatch",
            "platform": "youtube",
            "external_id": VIDEO,
            "exclude": ["literacy"],
        },
    )
    assert done.json() == {"exercise": None}
    assert client.post("/api/review/next", json={"context": "postwatch"}).status_code == 422
    assert client.get("/api/review/summary").json()["reviewed_today"] == 1

    missing = client.post(f"/api/videos/youtube/{VIDEO}/comprehension/0", json={"answer": "x"})
    assert missing.status_code == 404  # no questions were generated for this video


# ---------------------------------------------------------------- listening
def test_missed_line_replays_and_counts(client: TestClient) -> None:
    _with_transcript(client)  # lines at 0-3000 and 3000-6000
    url = f"/api/videos/youtube/{VIDEO}/missed"
    first = client.post(url, json={"position_ms": 4200}).json()
    assert first == {
        "start_ms": 3000,
        "end_ms": 6000,
        "text": "As Robinson said, children take a chance.",
        "count": 1,
        "replay_from_ms": 2700,
    }
    assert client.post(url, json={"position_ms": 5900}).json()["count"] == 2
    assert (
        client.post(url, json={"position_ms": 6800}).json()["start_ms"] == 3000
    )  # just after the end
    assert (
        client.post(url, json={"position_ms": 60000}).status_code == 404
    )  # nothing was said there
    assert [i["count"] for i in client.get(url).json()] == [3]


# ---------------------------------------------------------------- speech recognition
BV = "BV1xx411c7mD"
AUDIO_URL = "https://upos-sz-mirrorcos.bilivideo.com/x/1-30216.m4s?e=1"


def _wait_for(
    client: TestClient, url: str, done: Callable[[dict[str, object]], bool]
) -> dict[str, object]:
    import time

    for _ in range(250):
        body = client.get(url).json()
        if body is not None and done(body):
            return body  # type: ignore[no-any-return]
        time.sleep(0.02)
    raise AssertionError(f"{url} never reached the expected state")


def test_install_a_model_then_transcribe_a_video(client: TestClient) -> None:
    speech = client.get("/api/speech").json()
    assert speech["ready"] is False and speech["device"] is None and speech["gpu"]["name"] is None
    small = next(c for c in speech["components"] if c["id"] == "model-small")
    assert small["recommended"] and small["status"] == "not_installed"

    client.post("/api/videos/open", json={"platform": "bilibili", "external_id": BV, "title": "b"})
    url = f"/api/videos/bilibili/{BV}/transcription"
    r = client.post(url, json={"audio_urls": [AUDIO_URL], "duration_ms": 1000})
    assert r.status_code == 424 and "主页" in r.json()["detail"]

    assert client.post("/api/speech/components/model-small/install").status_code == 200
    _wait_for(client, "/api/speech", lambda b: b["ready"] is True)
    assert client.get(url).json() is None

    r = client.post(url, json={"audio_urls": [AUDIO_URL], "duration_ms": 1000})
    assert r.status_code == 200 and r.json()["status"] in (
        "queued",
        "downloading",
        "recognizing",
        "done",
    )
    job = _wait_for(client, url, lambda b: b["status"] == "done")
    assert job["transcript"]["kind"] == "asr" and job["model"] == "model-small"
    assert client.get(f"/api/videos/bilibili/{BV}/transcript").json()["kind"] == "asr"
    assert client.delete(url).json()["status"] == "done"  # nothing left to cancel

    assert client.post(url, json={"audio_urls": ["https://evil.example/a"]}).status_code == 422
    assert client.post(url, json={"audio_urls": []}).status_code == 422
    assert client.post("/api/speech/components/nope/install").status_code == 404
    assert client.post("/api/speech/components/model-small/cancel").json()["status"] == "installed"
    assert client.delete("/api/speech/components/model-small").json()["status"] == "not_installed"
    assert client.delete(f"/api/videos/bilibili/{BV}-p2/transcription").json() is None
