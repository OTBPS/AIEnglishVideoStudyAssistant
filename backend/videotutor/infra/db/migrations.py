"""Schema migrations. The database's `PRAGMA user_version` is its schema version.

Rules (docs/ARCHITECTURE.md, "兼容性策略"):

* append only -- never edit or reorder a migration that has shipped;
* each migration is one transaction: it applies completely or not at all;
* the database is backed up before the first migration runs on it;
* a database whose version is higher than len(MIGRATIONS) was written by a
  newer VideoTutor: it is opened read-only, never migrated "down".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        "videos, transcripts, watch sessions",
        """
        CREATE TABLE videos (
            id              INTEGER PRIMARY KEY,
            platform        TEXT NOT NULL CHECK (platform IN ('youtube', 'bilibili')),
            external_id     TEXT NOT NULL,
            title           TEXT NOT NULL DEFAULT '',
            url             TEXT NOT NULL DEFAULT '',
            duration_ms     INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
            first_seen_at   TEXT NOT NULL,
            last_opened_at  TEXT NOT NULL,
            UNIQUE (platform, external_id)
        );

        CREATE TABLE transcripts (
            id          INTEGER PRIMARY KEY,
            video_id    INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
            language    TEXT NOT NULL,
            kind        TEXT NOT NULL CHECK (kind IN ('manual', 'auto', 'asr')),
            source      TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            UNIQUE (video_id, language, kind)
        );

        CREATE TABLE segments (
            transcript_id  INTEGER NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
            seq            INTEGER NOT NULL,
            start_ms       INTEGER NOT NULL CHECK (start_ms >= 0),
            end_ms         INTEGER NOT NULL CHECK (end_ms > start_ms),
            text           TEXT NOT NULL,
            PRIMARY KEY (transcript_id, seq)
        ) WITHOUT ROWID;

        CREATE TABLE watch_sessions (
            id               INTEGER PRIMARY KEY,
            video_id         INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
            state            TEXT NOT NULL
                             CHECK (state IN ('open', 'playing', 'paused', 'ended', 'closed')),
            started_at       TEXT NOT NULL,
            ended_at         TEXT,
            watched_ms       INTEGER NOT NULL DEFAULT 0,
            max_position_ms  INTEGER NOT NULL DEFAULT 0,
            last_position_ms INTEGER
        );
        CREATE INDEX watch_sessions_video ON watch_sessions (video_id, started_at);
        CREATE INDEX watch_sessions_state ON watch_sessions (state);

        CREATE TABLE watch_events (
            id           INTEGER PRIMARY KEY,
            session_id   INTEGER NOT NULL REFERENCES watch_sessions(id) ON DELETE CASCADE,
            type         TEXT NOT NULL,
            position_ms  INTEGER NOT NULL,
            value        REAL,
            at           TEXT NOT NULL
        );
        CREATE INDEX watch_events_session ON watch_events (session_id, id);
        """,
    ),
)


def latest_version(migrations: tuple[Migration, ...] = MIGRATIONS) -> int:
    return len(migrations)


def check_contiguous(migrations: tuple[Migration, ...]) -> None:
    for i, m in enumerate(migrations, start=1):
        if m.version != i:
            raise ValueError(f"migration #{i} has version {m.version}; versions must be 1..N")
