"""In-watch: "I did not catch that" -- replay the line and remember it."""

from __future__ import annotations

from fastapi import APIRouter

from videotutor.app.listening import ListeningService
from videotutor.domain.listening import ListeningIssue
from videotutor.domain.video import Platform

from ..schemas import ListeningIssueOut, MissedIn
from .common import video_ref


def _out(i: ListeningIssue) -> ListeningIssueOut:
    return ListeningIssueOut(
        start_ms=i.start_ms,
        end_ms=i.end_ms,
        text=i.text,
        count=i.count,
        replay_from_ms=i.replay_from(),
    )


def router(listening: ListeningService) -> APIRouter:
    r = APIRouter(prefix="/api/videos/{platform}/{external_id}/missed")

    @r.post("")
    def missed(platform: Platform, external_id: str, body: MissedIn) -> ListeningIssueOut:
        return _out(listening.missed(video_ref(platform, external_id), body.position_ms))

    @r.get("")
    def issues(platform: Platform, external_id: str) -> list[ListeningIssueOut]:
        return [_out(i) for i in listening.issues(video_ref(platform, external_id))]

    return r
