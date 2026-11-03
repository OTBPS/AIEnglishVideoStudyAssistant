from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from videotutor.app.settings import MIGRATIONS, SCHEMA_VERSION, Settings
from videotutor.infra.settings_store import JsonSettingsStore, SettingsReadOnlyError


def _write(path: Path, doc: Any) -> None:
    path.write_text(json.dumps(doc), encoding="utf-8")


def _read(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_migration_chain_is_complete() -> None:
    assert sorted(MIGRATIONS) == list(range(1, SCHEMA_VERSION))


def test_missing_file_creates_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    report = JsonSettingsStore(path).load()
    assert report.created
    assert _read(path) == {"schema_version": SCHEMA_VERSION, **Settings().model_dump()}


def test_roundtrip_and_update(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = JsonSettingsStore(path)
    store.load()
    store.update(first_run_done=True)
    fresh = JsonSettingsStore(path)
    fresh.load()
    assert fresh.current().first_run_done is True
    with pytest.raises(ValidationError):
        store.update(first_run_done="yes")  # strict: no silent coercion
    assert not list(tmp_path.glob("*.tmp")), "atomic write leaves no temp file"


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"schema_version": "one"}', ""])
def test_corrupt_file_is_moved_aside(tmp_path: Path, content: str) -> None:
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")
    report = JsonSettingsStore(path).load()
    assert report.corrupt_backup is not None
    assert report.corrupt_backup.read_text(encoding="utf-8") == content
    assert _read(path)["schema_version"] == SCHEMA_VERSION
    assert report.notes


def test_one_invalid_field_falls_back_alone(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    _write(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "open_home_on_start": False,
            "first_run_done": "maybe",
            "from_the_future": 1,
        },
    )
    store = JsonSettingsStore(path)
    report = store.load()
    assert report.dropped_fields == ("first_run_done",)
    assert store.current().open_home_on_start is False  # the valid field survived
    assert store.current().first_run_done is False
    assert "from_the_future" not in _read(path)


def test_migration_runs_and_keeps_a_backup(tmp_path: Path) -> None:
    def v1_to_v2(doc: dict[str, Any]) -> dict[str, Any]:
        doc["open_home_on_start"] = doc.pop("show_home")
        return doc

    path = tmp_path / "settings.json"
    _write(path, {"schema_version": 1, "show_home": False})
    store = JsonSettingsStore(path, schema_version=2, migrations={1: v1_to_v2})
    report = store.load()
    assert report.migrated_from == 1
    assert store.current().open_home_on_start is False
    assert _read(path)["schema_version"] == 2
    assert _read(tmp_path / "settings.json.v1.bak") == {"schema_version": 1, "show_home": False}


def test_broken_migration_recovers_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    _write(path, {"schema_version": 1})
    report = JsonSettingsStore(path, schema_version=3, migrations={1: lambda d: d}).load()
    assert report.corrupt_backup is not None  # no step 2 -> 3


def test_newer_file_is_read_but_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    doc = {"schema_version": SCHEMA_VERSION + 1, "first_run_done": True, "new_thing": [1]}
    _write(path, doc)
    store = JsonSettingsStore(path)
    report = store.load()
    assert report.read_only and store.read_only
    assert store.current().first_run_done is True
    with pytest.raises(SettingsReadOnlyError):
        store.update(open_home_on_start=False)
    assert _read(path) == doc
