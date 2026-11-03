"""SQLite implementations of the repository ports in app/ports.py.

Rows are turned into domain objects here and nowhere else; nothing above this
module sees a sqlite3.Row. Timestamps are stored as ISO-8601 UTC strings.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from videotutor.app.ports import RecentVideo, TranscriptInfo
from videotutor.domain.transcript import Segment, Transcript
from videotutor.domain.video import Platform, TrackKind, Video, VideoRef
from videotutor.domain.watch import WatchEvent, WatchSession, WatchState

from .database import Database

# Which transcript a learner sees first: human-made, then the platform's
# recognition, then our own; within a kind, plain "en" before "en-XX".
_BEST_ORDER = """
    ORDER BY CASE kind WHEN 'manual' THEN 0 WHEN 'auto' THEN 1 ELSE 2 END,
             CASE WHEN language = 'en' THEN 0 ELSE 1 END,
             created_at DESC
    LIMIT 1
"""


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _dt(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _video(row: sqlite3.Row) -> Video:
    return Video(
        id=row["id"],
        ref=VideoRef(Platform(row["platform"]), row["external_id"]),
        title=row["title"],
        url=row["url"],
        duration_ms=row["duration_ms"],
    )


class SqliteVideoRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(
        self, ref: VideoRef, title: str, url: str, duration_ms: int | None, now: datetime
    ) -> Video:
        with self._db.write() as c:
            c.execute(
                """
                INSERT INTO videos (platform, external_id, title, url, duration_ms,
                                    first_seen_at, last_opened_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (platform, external_id) DO UPDATE SET
                    title = CASE WHEN excluded.title != '' THEN excluded.title ELSE title END,
                    url = CASE WHEN excluded.url != '' THEN excluded.url ELSE url END,
                    duration_ms = COALESCE(excluded.duration_ms, duration_ms),
                    last_opened_at = excluded.last_opened_at
                """,
                (ref.platform.value, ref.external_id, title, url, duration_ms, _ts(now), _ts(now)),
            )
            row = c.execute(
                "SELECT * FROM videos WHERE platform = ? AND external_id = ?",
                (ref.platform.value, ref.external_id),
            ).fetchone()
        return _video(row)

    def get(self, ref: VideoRef) -> Video | None:
        with self._db.read() as c:
            row = c.execute(
                "SELECT * FROM videos WHERE platform = ? AND external_id = ?",
                (ref.platform.value, ref.external_id),
            ).fetchone()
        return _video(row) if row else None

    def recent(self, limit: int) -> list[RecentVideo]:
        with self._db.read() as c:
            rows = c.execute(
                """
                SELECT v.*,
                       COALESCE(SUM(s.watched_ms), 0) AS watched,
                       COALESCE(MAX(s.max_position_ms), 0) AS max_pos
                FROM videos v LEFT JOIN watch_sessions s ON s.video_id = v.id
                GROUP BY v.id
                ORDER BY v.last_opened_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            transcripts = SqliteTranscriptRepository(self._db)
            return [
                RecentVideo(
                    video=_video(r),
                    last_opened_at=datetime.fromisoformat(r["last_opened_at"]),
                    watched_ms=r["watched"],
                    max_position_ms=r["max_pos"],
                    transcript=transcripts.best_info(r["id"]),
                )
                for r in rows
            ]


class SqliteTranscriptRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, video_id: int, transcript: Transcript, now: datetime) -> TranscriptInfo:
        with self._db.write() as c:
            c.execute(
                "DELETE FROM transcripts WHERE video_id = ? AND language = ? AND kind = ?",
                (video_id, transcript.language, transcript.kind.value),
            )
            cur = c.execute(
                "INSERT INTO transcripts (video_id, language, kind, source, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (video_id, transcript.language, transcript.kind.value, transcript.source, _ts(now)),
            )
            tid = cur.lastrowid
            c.executemany(
                "INSERT INTO segments (transcript_id, seq, start_ms, end_ms, text) "
                "VALUES (?, ?, ?, ?, ?)",
                [(tid, i, s.start_ms, s.end_ms, s.text) for i, s in enumerate(transcript.segments)],
            )
        return TranscriptInfo(
            transcript.language, transcript.kind, transcript.source, len(transcript.segments)
        )

    def _best_row(self, c: sqlite3.Connection, video_id: int) -> sqlite3.Row | None:
        row: sqlite3.Row | None = c.execute(
            f"SELECT * FROM transcripts WHERE video_id = ? {_BEST_ORDER}",  # noqa: S608 -- constant
            (video_id,),
        ).fetchone()
        return row

    def best(self, video_id: int) -> Transcript | None:
        with self._db.read() as c:
            row = self._best_row(c, video_id)
            if row is None:
                return None
            segs = c.execute(
                "SELECT start_ms, end_ms, text FROM segments WHERE transcript_id = ? ORDER BY seq",
                (row["id"],),
            ).fetchall()
        return Transcript(
            language=row["language"],
            kind=TrackKind(row["kind"]),
            source=row["source"],
            segments=tuple(Segment(s["start_ms"], s["end_ms"], s["text"]) for s in segs),
        )

    def best_info(self, video_id: int) -> TranscriptInfo | None:
        with self._db.read() as c:
            row = self._best_row(c, video_id)
            if row is None:
                return None
            count = c.execute(
                "SELECT COUNT(*) FROM segments WHERE transcript_id = ?", (row["id"],)
            ).fetchone()[0]
        return TranscriptInfo(row["language"], TrackKind(row["kind"]), row["source"], count)


class SqliteWatchRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, video_id: int, now: datetime) -> WatchSession:
        with self._db.write() as c:
            cur = c.execute(
                "INSERT INTO watch_sessions (video_id, state, started_at) VALUES (?, ?, ?)",
                (video_id, WatchState.OPEN.value, _ts(now)),
            )
            sid = cur.lastrowid
        if sid is None:
            raise RuntimeError("insert returned no row id")
        return WatchSession(id=sid, video_id=video_id, state=WatchState.OPEN, started_at=now)

    def get(self, session_id: int) -> WatchSession | None:
        with self._db.read() as c:
            r = c.execute("SELECT * FROM watch_sessions WHERE id = ?", (session_id,)).fetchone()
        if r is None:
            return None
        return WatchSession(
            id=r["id"],
            video_id=r["video_id"],
            state=WatchState(r["state"]),
            started_at=datetime.fromisoformat(r["started_at"]),
            ended_at=_dt(r["ended_at"]),
            watched_ms=r["watched_ms"],
            max_position_ms=r["max_position_ms"],
            last_position_ms=r["last_position_ms"],
        )

    def save(self, session: WatchSession, events: list[WatchEvent]) -> None:
        with self._db.write() as c:
            c.execute(
                """
                UPDATE watch_sessions SET state = ?, ended_at = ?, watched_ms = ?,
                       max_position_ms = ?, last_position_ms = ?
                WHERE id = ?
                """,
                (
                    session.state.value,
                    _ts(session.ended_at) if session.ended_at else None,
                    session.watched_ms,
                    session.max_position_ms,
                    session.last_position_ms,
                    session.id,
                ),
            )
            c.executemany(
                "INSERT INTO watch_events (session_id, type, position_ms, value, at) "
                "VALUES (?, ?, ?, ?, ?)",
                [(session.id, e.type.value, e.position_ms, e.value, _ts(e.at)) for e in events],
            )

    def close_unfinished(self, now: datetime) -> int:
        with self._db.write() as c:
            cur = c.execute(
                "UPDATE watch_sessions SET state = 'closed', ended_at = ?, last_position_ms = NULL "
                "WHERE state != 'closed'",
                (_ts(now),),
            )
        return cur.rowcount
