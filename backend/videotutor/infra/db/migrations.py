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
    Migration(
        2,
        "word knowledge, glosses, pre-watch guides",
        """
        CREATE TABLE word_status (
            lemma       TEXT PRIMARY KEY,
            status      TEXT NOT NULL CHECK (status IN ('known', 'learning', 'ignored')),
            updated_at  TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE glosses (
            video_id        INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
            lemma           TEXT NOT NULL,
            part_of_speech  TEXT NOT NULL,
            definition_en   TEXT NOT NULL,
            definition_zh   TEXT NOT NULL,
            example         TEXT NOT NULL,
            model           TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            PRIMARY KEY (video_id, lemma)
        ) WITHOUT ROWID;

        CREATE TABLE prewatch_guides (
            video_id           INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
            prediction_prompt  TEXT NOT NULL,
            questions_json     TEXT NOT NULL,
            partial            INTEGER NOT NULL DEFAULT 0,
            model              TEXT NOT NULL,
            created_at         TEXT NOT NULL
        );
        """,
    ),
    Migration(
        3,
        "review cards and attempts",
        """
        CREATE TABLE cards (
            lemma            TEXT PRIMARY KEY,
            source_video_id  INTEGER REFERENCES videos(id) ON DELETE SET NULL,
            phase            TEXT NOT NULL CHECK (phase IN ('learning', 'review', 'relearning')),
            due              TEXT NOT NULL,
            stability        REAL,
            difficulty       REAL,
            step             INTEGER,
            last_review      TEXT,
            reps             INTEGER NOT NULL DEFAULT 0,
            lapses           INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE INDEX cards_due ON cards (due);
        CREATE INDEX cards_video ON cards (source_video_id);

        CREATE TABLE attempts (
            id           INTEGER PRIMARY KEY,
            lemma        TEXT,
            video_id     INTEGER REFERENCES videos(id) ON DELETE SET NULL,
            kind         TEXT NOT NULL,
            prompt       TEXT NOT NULL,
            answer       TEXT NOT NULL,
            grade        TEXT NOT NULL CHECK (grade IN ('again', 'hard', 'good', 'easy')),
            feedback     TEXT NOT NULL DEFAULT '',
            context      TEXT NOT NULL CHECK (context IN ('postwatch', 'review')),
            duration_ms  INTEGER,
            at           TEXT NOT NULL
        );
        CREATE INDEX attempts_at ON attempts (at);
        CREATE INDEX attempts_lemma ON attempts (lemma, at);
        """,
    ),
)


def latest_version(migrations: tuple[Migration, ...] = MIGRATIONS) -> int:
    return len(migrations)


def check_contiguous(migrations: tuple[Migration, ...]) -> None:
    for i, m in enumerate(migrations, start=1):
        if m.version != i:
            raise ValueError(f"migration #{i} has version {m.version}; versions must be 1..N")
