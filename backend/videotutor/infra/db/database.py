"""The learning database file: open, recover, migrate, and serialize access.

Opening never silently loses data:

* missing         -> created at the latest schema;
* unreadable      -> the file (and its -wal/-shm) is moved aside as
                     <name>.corrupt-<time> and a fresh database is created;
* older schema    -> backed up as <name>.v<N>.bak, then migrated step by step;
* newer schema    -> opened with query_only: readable, never written, so a
                     later upgrade finds the newer data intact;
* failed migration-> the transaction rolls back and opening fails loudly --
                     that is a bug to fix, not something to paper over.

One connection, guarded by a lock. Writes from FastAPI's worker threads and
the task loop are serialized; for a single-user app that costs nothing and
removes a whole class of "database is locked" failures.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from videotutor.app.errors import StorageReadOnlyError

from .migrations import MIGRATIONS, Migration, check_contiguous

log = logging.getLogger("db")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenReport:
    version: int
    created: bool = False
    migrated_from: int | None = None
    backup: Path | None = None
    corrupt_backup: Path | None = None
    read_only: bool = False


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


class Database:
    def __init__(self, conn: sqlite3.Connection, path: Path, *, read_only: bool) -> None:
        self._conn = conn
        self.path = path
        self.read_only = read_only
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ open
    @classmethod
    def open(
        cls, path: Path, migrations: tuple[Migration, ...] = MIGRATIONS
    ) -> tuple[Database, OpenReport]:
        check_contiguous(migrations)
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists() and path.stat().st_size > 0
        corrupt_backup: Path | None = None

        conn = _connect(path)
        try:
            version = cls._health_check(conn)
        except sqlite3.DatabaseError as exc:
            conn.close()
            corrupt_backup = cls._move_aside(path)
            log.error("database unreadable (%s); moved to %s", exc, corrupt_backup.name)
            conn = _connect(path)
            version, existed = 0, False

        target = len(migrations)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")

        if version > target:
            conn.execute("PRAGMA query_only = ON")
            log.warning("database schema %d is newer than %d: read-only", version, target)
            report = OpenReport(version, read_only=True, corrupt_backup=corrupt_backup)
            return cls(conn, path, read_only=True), report

        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        backup: Path | None = None
        migrated_from: int | None = None
        if version < target:
            if existed and version > 0:
                backup = path.with_name(f"{path.name}.v{version}.bak")
                with contextlib.closing(sqlite3.connect(backup)) as dst:
                    conn.backup(dst)
                migrated_from = version
            for m in migrations[version:]:
                cls._apply(conn, m)
            log.info("database schema %d -> %d", version, target)

        report = OpenReport(
            target,
            created=not existed,
            migrated_from=migrated_from,
            backup=backup,
            corrupt_backup=corrupt_backup,
        )
        return cls(conn, path, read_only=False), report

    @staticmethod
    def _health_check(conn: sqlite3.Connection) -> int:
        result = conn.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise sqlite3.DatabaseError(f"quick_check: {result}")
        return int(conn.execute("PRAGMA user_version").fetchone()[0])

    @staticmethod
    def _move_aside(path: Path) -> Path:
        target = path.with_name(f"{path.name}.corrupt-{_stamp()}")
        os.replace(path, target)
        for suffix in ("-wal", "-shm"):
            side = path.with_name(path.name + suffix)
            if side.exists():
                os.replace(side, target.with_name(target.name + suffix))
        return target

    @staticmethod
    def _apply(conn: sqlite3.Connection, m: Migration) -> None:
        try:
            conn.executescript(
                f"BEGIN IMMEDIATE;\n{m.sql}\nPRAGMA user_version = {m.version};\nCOMMIT;"
            )
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"migration {m.version} ({m.name}) failed: {exc}") from exc

    # ------------------------------------------------------------------ use
    @contextlib.contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._conn

    @contextlib.contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One transaction: commits on success, rolls back on any exception."""
        if self.read_only:
            raise StorageReadOnlyError(
                f"{self.path.name} was written by a newer VideoTutor; it is read-only here"
            )
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            if not self.read_only:
                with contextlib.suppress(sqlite3.Error):
                    self._conn.execute("PRAGMA optimize")
            self._conn.close()
