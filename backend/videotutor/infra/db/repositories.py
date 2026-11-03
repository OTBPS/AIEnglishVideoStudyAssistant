"""SQLite implementations of the repository ports in app/ports.py.

Rows are turned into domain objects here and nowhere else; nothing above this
module sees a sqlite3.Row. Timestamps are stored as ISO-8601 UTC strings.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from videotutor.app.ports import Attempt, RecentVideo, TranscriptInfo
from videotutor.domain.listening import ListeningIssue
from videotutor.domain.prewatch import Gloss, PrewatchGuide, Question
from videotutor.domain.review import Card, SrsPhase, SrsState
from videotutor.domain.transcript import Segment, Transcript
from videotutor.domain.video import Platform, TrackKind, Video, VideoRef
from videotutor.domain.vocab import WordStatus
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


class SqliteWordStatusRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def all(self) -> dict[str, WordStatus]:
        with self._db.read() as c:
            rows = c.execute("SELECT lemma, status FROM word_status").fetchall()
        return {r["lemma"]: WordStatus(r["status"]) for r in rows}

    def set(self, lemma: str, status: WordStatus | None, now: datetime) -> None:
        with self._db.write() as c:
            if status is None:
                c.execute("DELETE FROM word_status WHERE lemma = ?", (lemma,))
            else:
                c.execute(
                    "INSERT INTO word_status (lemma, status, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (lemma) DO UPDATE SET status = excluded.status, "
                    "updated_at = excluded.updated_at",
                    (lemma, status.value, _ts(now)),
                )


class SqlitePrewatchRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def glosses(self, video_id: int) -> dict[str, Gloss]:
        with self._db.read() as c:
            rows = c.execute("SELECT * FROM glosses WHERE video_id = ?", (video_id,)).fetchall()
        return {
            r["lemma"]: Gloss(
                r["lemma"],
                r["part_of_speech"],
                r["definition_en"],
                r["definition_zh"],
                r["example"],
            )
            for r in rows
        }

    def save_glosses(self, video_id: int, glosses: list[Gloss], model: str, now: datetime) -> None:
        with self._db.write() as c:
            c.executemany(
                "INSERT OR REPLACE INTO glosses (video_id, lemma, part_of_speech, definition_en, "
                "definition_zh, example, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        video_id,
                        g.lemma,
                        g.part_of_speech,
                        g.definition_en,
                        g.definition_zh,
                        g.example,
                        model,
                        _ts(now),
                    )
                    for g in glosses
                ],
            )

    def latest_gloss(self, lemma: str) -> Gloss | None:
        with self._db.read() as c:
            r = c.execute(
                "SELECT * FROM glosses WHERE lemma = ? ORDER BY created_at DESC LIMIT 1", (lemma,)
            ).fetchone()
        if r is None:
            return None
        return Gloss(
            r["lemma"], r["part_of_speech"], r["definition_en"], r["definition_zh"], r["example"]
        )

    def meaning_pool(self, exclude: str, limit: int) -> list[str]:
        with self._db.read() as c:
            rows = c.execute(
                "SELECT DISTINCT definition_zh FROM glosses WHERE lemma != ? "
                "ORDER BY created_at DESC LIMIT ?",
                (exclude, limit),
            ).fetchall()
        return [r[0] for r in rows]

    def guide(self, video_id: int) -> PrewatchGuide | None:
        with self._db.read() as c:
            r = c.execute(
                "SELECT * FROM prewatch_guides WHERE video_id = ?", (video_id,)
            ).fetchone()
        if r is None:
            return None
        questions = tuple(
            Question(q["question"], q["hint_zh"]) for q in json.loads(r["questions_json"])
        )
        return PrewatchGuide(r["prediction_prompt"], questions, bool(r["partial"]))

    def save_guide(self, video_id: int, guide: PrewatchGuide, model: str, now: datetime) -> None:
        questions = json.dumps(
            [{"question": q.question, "hint_zh": q.hint_zh} for q in guide.questions],
            ensure_ascii=False,
        )
        with self._db.write() as c:
            c.execute(
                "INSERT OR REPLACE INTO prewatch_guides (video_id, prediction_prompt, "
                "questions_json, partial, model, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    video_id,
                    guide.prediction_prompt,
                    questions,
                    int(guide.based_on_partial_transcript),
                    model,
                    _ts(now),
                ),
            )


def _card(r: sqlite3.Row) -> Card:
    return Card(
        lemma=r["lemma"],
        srs=SrsState(
            phase=SrsPhase(r["phase"]),
            due=datetime.fromisoformat(r["due"]),
            stability=r["stability"],
            difficulty=r["difficulty"],
            step=r["step"],
            last_review=_dt(r["last_review"]),
        ),
        source_video_id=r["source_video_id"],
        reps=r["reps"],
        lapses=r["lapses"],
        created_at=datetime.fromisoformat(r["created_at"]),
    )


class SqliteCardRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, lemma: str) -> Card | None:
        with self._db.read() as c:
            r = c.execute("SELECT * FROM cards WHERE lemma = ?", (lemma,)).fetchone()
        return _card(r) if r else None

    def save(self, card: Card) -> None:
        s = card.srs
        with self._db.write() as c:
            c.execute(
                "INSERT OR REPLACE INTO cards (lemma, source_video_id, phase, due, stability, "
                "difficulty, step, last_review, reps, lapses, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    card.lemma,
                    card.source_video_id,
                    s.phase.value,
                    _ts(s.due),
                    s.stability,
                    s.difficulty,
                    s.step,
                    _ts(s.last_review) if s.last_review else None,
                    card.reps,
                    card.lapses,
                    _ts(card.created_at),
                ),
            )

    def delete(self, lemma: str) -> None:
        with self._db.write() as c:
            c.execute("DELETE FROM cards WHERE lemma = ?", (lemma,))

    def due(self, now: datetime, limit: int) -> list[Card]:
        with self._db.read() as c:
            rows = c.execute(
                "SELECT * FROM cards WHERE due <= ? ORDER BY due LIMIT ?", (_ts(now), limit)
            ).fetchall()
        return [_card(r) for r in rows]

    def for_video(self, video_id: int) -> list[Card]:
        with self._db.read() as c:
            rows = c.execute(
                "SELECT * FROM cards WHERE source_video_id = ? ORDER BY created_at", (video_id,)
            ).fetchall()
        return [_card(r) for r in rows]

    def counts(self, now: datetime) -> tuple[int, int]:
        with self._db.read() as c:
            due = c.execute("SELECT COUNT(*) FROM cards WHERE due <= ?", (_ts(now),)).fetchone()[0]
            total = c.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        return int(due), int(total)


class SqliteAttemptRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, a: Attempt) -> None:
        with self._db.write() as c:
            c.execute(
                "INSERT INTO attempts (lemma, video_id, kind, prompt, answer, grade, feedback, "
                "context, duration_ms, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    a.lemma,
                    a.video_id,
                    a.kind,
                    a.prompt,
                    a.answer,
                    a.grade.value,
                    a.feedback,
                    a.context,
                    a.duration_ms,
                    _ts(a.at),
                ),
            )

    def count_since(self, since: datetime) -> int:
        with self._db.read() as c:
            return int(
                c.execute("SELECT COUNT(*) FROM attempts WHERE at >= ?", (_ts(since),)).fetchone()[
                    0
                ]
            )


def _issue(r: sqlite3.Row) -> ListeningIssue:
    return ListeningIssue(
        r["video_id"],
        r["start_ms"],
        r["end_ms"],
        r["text"],
        r["count"],
        datetime.fromisoformat(r["first_at"]),
        datetime.fromisoformat(r["last_at"]),
    )


class SqliteListeningIssueRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(
        self, video_id: int, start_ms: int, end_ms: int, text: str, now: datetime
    ) -> ListeningIssue:
        with self._db.write() as c:
            c.execute(
                "INSERT INTO listening_issues "
                "(video_id, start_ms, end_ms, text, first_at, last_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (video_id, start_ms) DO UPDATE SET "
                "count = count + 1, last_at = excluded.last_at",
                (video_id, start_ms, end_ms, text, _ts(now), _ts(now)),
            )
            row = c.execute(
                "SELECT * FROM listening_issues WHERE video_id = ? AND start_ms = ?",
                (video_id, start_ms),
            ).fetchone()
        return _issue(row)

    def for_video(self, video_id: int) -> list[ListeningIssue]:
        with self._db.read() as c:
            rows = c.execute(
                "SELECT * FROM listening_issues WHERE video_id = ? ORDER BY start_ms", (video_id,)
            ).fetchall()
        return [_issue(r) for r in rows]
