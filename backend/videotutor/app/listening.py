"""In-watch listening help: replay the line just missed, and remember it."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from videotutor.domain.listening import ListeningIssue
from videotutor.domain.video import VideoRef

from .errors import NotFoundError
from .ports import ListeningIssueRepository
from .videos import VideoService, utc_now

# Pressing the key just after a line ended almost always means that line.
LOOKBACK_MS = 1500


class ListeningService:
    def __init__(
        self,
        videos: VideoService,
        issues: ListeningIssueRepository,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._videos = videos
        self._issues = issues
        self._now = now

    def missed(self, ref: VideoRef, position_ms: int) -> ListeningIssue:
        """The learner did not catch what was just said at `position_ms`."""
        video = self._videos.require_video(ref)
        transcript = self._videos.transcript(ref)
        index = transcript.index_at(position_ms)
        if index is None:
            # In a pause between lines: the line that just ended.
            index = transcript.index_at(max(0, position_ms - LOOKBACK_MS))
        if index is None:
            raise NotFoundError("no line is being spoken here")
        seg = transcript.segments[index]
        return self._issues.record(video.id, seg.start_ms, seg.end_ms, seg.text, self._now())

    def issues(self, ref: VideoRef) -> list[ListeningIssue]:
        return self._issues.for_video(self._videos.require_video(ref).id)
