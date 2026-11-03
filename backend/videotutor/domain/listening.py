"""Listening trouble spots: lines the learner could not catch while watching.

Decoding connected speech is where listening breaks down for most learners
(ARCHITECTURE.md, 学习方法依据); every "没听清" press marks the line being
spoken, and the lines collected this way become dictation material later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

REPLAY_LEAD_MS = 300  # start replays a little early: onsets are easy to miss


@dataclass(frozen=True)
class ListeningIssue:
    video_id: int
    start_ms: int
    end_ms: int
    text: str
    count: int  # how many times the learner asked to hear it again
    first_at: datetime
    last_at: datetime

    def replay_from(self) -> int:
        return max(0, self.start_ms - REPLAY_LEAD_MS)
