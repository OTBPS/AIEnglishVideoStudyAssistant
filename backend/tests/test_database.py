from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from videotutor.app.errors import StorageReadOnlyError
from videotutor.infra.db.database import Database, MigrationError
from videotutor.infra.db.migrations import MIGRATIONS, Migration, check_contiguous, latest_version

V1 = (Migration(1, "t", "CREATE TABLE t (x INTEGER);"),)
V2 = (*V1, Migration(2, "u", "ALTER TABLE t ADD COLUMN y TEXT DEFAULT 'new';"))


def _version(path: Path) -> int:
    with sqlite3.connect(path) as c:
        return int(c.execute("PRAGMA user_version").fetchone()[0])


def test_real_migrations_apply_to_a_fresh_database(tmp_path: Path) -> None:
    check_contiguous(MIGRATIONS)
    db, report = Database.open(tmp_path / "tutor.db")
    assert report.created and report.version == latest_version()
    with db.read() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"videos", "transcripts", "segments", "watch_sessions", "watch_events"} <= tables
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    db.close()
    db2, report2 = Database.open(tmp_path / "tutor.db")
    assert not report2.created and report2.migrated_from is None
    db2.close()


def test_upgrade_migrates_and_keeps_a_backup(tmp_path: Path) -> None:
    path = tmp_path / "tutor.db"
    db, _ = Database.open(path, V1)
    with db.write() as c:
        c.execute("INSERT INTO t (x) VALUES (7)")
    db.close()

    db, report = Database.open(path, V2)
    assert (report.migrated_from, report.version) == (1, 2)
    assert report.backup is not None and _version(report.backup) == 1
    with db.read() as c:
        assert tuple(c.execute("SELECT x, y FROM t").fetchone()) == (7, "new")
    db.close()


def test_newer_database_is_read_only_and_untouched(tmp_path: Path) -> None:
    path = tmp_path / "tutor.db"
    Database.open(path, V2)[0].close()
    db, report = Database.open(path, V1)
    assert report.read_only and db.read_only
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
    with pytest.raises(StorageReadOnlyError), db.write():
        pass
    db.close()
    assert _version(path) == 2


def test_corrupt_file_is_moved_aside(tmp_path: Path) -> None:
    path = tmp_path / "tutor.db"
    path.write_bytes(b"this is not a database at all" * 100)
    db, report = Database.open(path, V1)
    assert report.created and report.corrupt_backup is not None
    assert report.corrupt_backup.read_bytes().startswith(b"this is not")
    with db.write() as c:
        c.execute("INSERT INTO t (x) VALUES (1)")
    db.close()


def test_failed_migration_rolls_back_and_raises(tmp_path: Path) -> None:
    path = tmp_path / "tutor.db"
    Database.open(path, V1)[0].close()
    broken = (*V1, Migration(2, "broken", "ALTER TABLE t ADD COLUMN z; SELECT * FROM missing;"))
    with pytest.raises(MigrationError, match="broken"):
        Database.open(path, broken)
    assert _version(path) == 1
    with sqlite3.connect(path) as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(t)")]
    assert cols == ["x"], "the half-applied column was rolled back"


def test_write_rolls_back_on_error(tmp_path: Path) -> None:
    db, _ = Database.open(tmp_path / "tutor.db", V1)
    with pytest.raises(RuntimeError), db.write() as c:
        c.execute("INSERT INTO t (x) VALUES (1)")
        raise RuntimeError("boom")
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
    db.close()


def test_migration_list_must_be_contiguous() -> None:
    with pytest.raises(ValueError, match=r"1..N"):
        check_contiguous((Migration(2, "x", ""),))
