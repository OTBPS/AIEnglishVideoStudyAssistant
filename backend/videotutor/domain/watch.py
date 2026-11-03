"""A watch session: one sitting with one video, as an explicit state model.

    OPEN --play--> PLAYING --pause--> PAUSED --play--> PLAYING
                      |                  |
                      +------ended-------+--> ENDED --play--> PLAYING (rewatch)
    any state --close--> CLOSED (tab closed, navigated away, app restarted)

Events come from a browser tab and may arrive duplicated or out of order (a
pause after the video already ended, two plays in a row). An event that is not
valid in the current state changes nothing -- it is noise, not an error.

watched_ms counts only time actually played forward: seeking ahead does not
earn credit, and a gap longer than MAX_CREDIT_GAP_MS between two reports
(laptop asleep, tab throttled) is not credited either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from videotutor.core.statemachine import StateMachine

MAX_CREDIT_GAP_MS = 20_000


class WatchState(StrEnum):
    OPEN = "open"
    PLAYING = "playing"
    PAUSED = "paused"
    ENDED = "ended"
    CLOSED = "closed"


_TRANSITIONS: dict[WatchState, frozenset[WatchState]] = {
    WatchState.OPEN: frozenset({WatchState.PLAYING, WatchState.CLOSED}),
    WatchState.PLAYING: frozenset({WatchState.PAUSED, WatchState.ENDED, WatchState.CLOSED}),
    WatchState.PAUSED: frozenset({WatchState.PLAYING, WatchState.ENDED, WatchState.CLOSED}),
    WatchState.ENDED: frozenset({WatchState.PLAYING, WatchState.CLOSED}),
    WatchState.CLOSED: frozenset(),
}


class WatchEventType(StrEnum):
    PLAY = "play"
    PAUSE = "pause"
    SEEK = "seek"
    RATE = "rate"
    PROGRESS = "progress"
    ENDED = "ended"


@dataclass(frozen=True)
class WatchEvent:
    type: WatchEventType
    position_ms: int
    at: datetime
    value: float | None = None  # playback rate for RATE

    def __post_init__(self) -> None:
        if self.position_ms < 0:
            raise ValueError("negative position")


@dataclass
class WatchSession:
    id: int
    video_id: int
    state: WatchState
    started_at: datetime
    ended_at: datetime | None = None
    watched_ms: int = 0
    max_position_ms: int = 0
    last_position_ms: int | None = None  # last credited position while playing

    def apply(self, event: WatchEvent) -> bool:
        """Fold one event into the session. Returns False if it was ignored."""
        if self.state is WatchState.CLOSED:
            return False
        self.max_position_ms = max(self.max_position_ms, event.position_ms)
        match event.type:
            case WatchEventType.PLAY:
                if not self._move(WatchState.PLAYING):
                    return False
                self.last_position_ms = event.position_ms
            case WatchEventType.PAUSE:
                self._credit(event.position_ms)
                if not self._move(WatchState.PAUSED):
                    return False
            case WatchEventType.ENDED:
                self._credit(event.position_ms)
                if not self._move(WatchState.ENDED):
                    return False
            case WatchEventType.PROGRESS:
                if self.state is not WatchState.PLAYING:
                    return False
                self._credit(event.position_ms)
            case WatchEventType.SEEK:
                # Jumping is not watching: restart crediting from the new spot.
                self.last_position_ms = (
                    event.position_ms if self.state is WatchState.PLAYING else None
                )
            case WatchEventType.RATE:
                pass  # recorded for later analysis; no effect on state
        return True

    def close(self, at: datetime) -> None:
        if self.state is not WatchState.CLOSED:
            self._move(WatchState.CLOSED)
            self.ended_at = at

    def _credit(self, position_ms: int) -> None:
        if self.state is WatchState.PLAYING and self.last_position_ms is not None:
            delta = position_ms - self.last_position_ms
            if 0 < delta <= MAX_CREDIT_GAP_MS:
                self.watched_ms += delta
        self.last_position_ms = position_ms if self.state is WatchState.PLAYING else None

    def _move(self, target: WatchState) -> bool:
        sm = StateMachine(self.state, _TRANSITIONS)
        if not sm.can(target):
            return False
        sm.transition(target)
        self.state = target
        if target is not WatchState.PLAYING:
            self.last_position_ms = None
        return True
