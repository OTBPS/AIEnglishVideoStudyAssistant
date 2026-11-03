"""Application lifecycle as an explicit state model.

    CREATED -> STARTING -> RUNNING -> STOPPING -> STOPPED
                   \\-> FAILED ------/

Components register a stop hook as they come up; stop() runs the hooks in
reverse order (last started, first stopped), isolates their failures from
one another, and is idempotent -- the tray's Quit, Ctrl+C and a startup
failure may all call it, and only the first does anything.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from enum import Enum

from videotutor.core.statemachine import StateMachine

log = logging.getLogger("lifecycle")


class AppState(Enum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


_TRANSITIONS: dict[AppState, frozenset[AppState]] = {
    AppState.CREATED: frozenset({AppState.STARTING}),
    AppState.STARTING: frozenset({AppState.RUNNING, AppState.FAILED}),
    AppState.RUNNING: frozenset({AppState.STOPPING}),
    AppState.FAILED: frozenset({AppState.STOPPING}),
    AppState.STOPPING: frozenset({AppState.STOPPED}),
    AppState.STOPPED: frozenset(),
}


class Lifecycle:
    def __init__(self) -> None:
        self._sm = StateMachine(AppState.CREATED, _TRANSITIONS)
        self._hooks: list[tuple[str, Callable[[], None]]] = []
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._sm.on_change(lambda old, new: log.info("app %s -> %s", old.value, new.value))

    @property
    def state(self) -> AppState:
        return self._sm.state

    def begin_start(self) -> None:
        self._sm.transition(AppState.STARTING)

    def mark_running(self) -> None:
        self._sm.transition(AppState.RUNNING)

    def mark_failed(self) -> None:
        self._sm.transition(AppState.FAILED)

    def add_stop_hook(self, name: str, hook: Callable[[], None]) -> None:
        with self._lock:
            if self._sm.state not in (AppState.STARTING, AppState.RUNNING):
                raise RuntimeError(f"cannot add stop hook {name!r} in state {self.state.value}")
            self._hooks.append((name, hook))

    def stop(self) -> list[str]:
        """Run stop hooks once, newest first. Returns names of hooks that failed."""
        with self._lock:
            if self._sm.state in (AppState.CREATED, AppState.STOPPING, AppState.STOPPED):
                return []
            self._sm.transition(AppState.STOPPING)
            hooks = list(reversed(self._hooks))
            self._hooks.clear()
        failed: list[str] = []
        for name, hook in hooks:
            try:
                hook()
            except Exception:
                log.exception("stop hook %r failed", name)
                failed.append(name)
        self._sm.transition(AppState.STOPPED)
        self._stopped.set()
        return failed

    def wait_stopped(self, timeout: float | None = None) -> bool:
        return self._stopped.wait(timeout)
