from __future__ import annotations

from enum import Enum

import pytest

from videotutor.core.statemachine import InvalidTransition, StateMachine


class Light(Enum):
    RED = 1
    GREEN = 2
    YELLOW = 3


TABLE = {
    Light.RED: frozenset({Light.GREEN}),
    Light.GREEN: frozenset({Light.YELLOW}),
    Light.YELLOW: frozenset({Light.RED}),
}


def test_allowed_transitions_notify_listeners() -> None:
    sm = StateMachine(Light.RED, TABLE)
    seen: list[tuple[Light, Light]] = []
    sm.on_change(lambda old, new: seen.append((old, new)))
    sm.transition(Light.GREEN)
    sm.transition(Light.YELLOW)
    assert sm.state is Light.YELLOW
    assert seen == [(Light.RED, Light.GREEN), (Light.GREEN, Light.YELLOW)]


def test_illegal_transition_raises_and_keeps_state() -> None:
    sm = StateMachine(Light.RED, TABLE)
    assert not sm.can(Light.YELLOW)
    with pytest.raises(InvalidTransition, match="RED -> YELLOW"):
        sm.transition(Light.YELLOW)
    assert sm.state is Light.RED


def test_incomplete_table_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="YELLOW"):
        StateMachine(Light.RED, {Light.RED: frozenset(), Light.GREEN: frozenset()})
