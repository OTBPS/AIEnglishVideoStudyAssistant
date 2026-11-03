"""A small, explicit state machine.

Every important process (app lifecycle, background tasks, and from M1 the
learning session) gets one of these instead of a handful of booleans whose
combinations nobody enumerated. Illegal transitions raise immediately, so a
bug shows up where it happens rather than as a weird state later.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from enum import Enum


class InvalidTransition(RuntimeError):
    pass


class StateMachine[S: Enum]:
    def __init__(self, initial: S, transitions: Mapping[S, frozenset[S]]) -> None:
        missing = set(type(initial)) - set(transitions)
        if missing:
            raise ValueError(f"transition table has no entry for {sorted(m.name for m in missing)}")
        self._state = initial
        self._transitions = transitions
        self._listeners: list[Callable[[S, S], None]] = []
        self._lock = threading.RLock()

    @property
    def state(self) -> S:
        return self._state

    def can(self, target: S) -> bool:
        return target in self._transitions[self._state]

    def transition(self, target: S) -> None:
        with self._lock:
            old = self._state
            if target not in self._transitions[old]:
                raise InvalidTransition(f"{old.name} -> {target.name} is not allowed")
            self._state = target
            listeners = list(self._listeners)
        for listener in listeners:
            listener(old, target)

    def on_change(self, listener: Callable[[S, S], None]) -> None:
        with self._lock:
            self._listeners.append(listener)
