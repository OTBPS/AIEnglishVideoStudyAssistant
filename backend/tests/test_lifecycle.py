from __future__ import annotations

import pytest

from videotutor.app.lifecycle import AppState, Lifecycle
from videotutor.core.statemachine import InvalidTransition


def test_hooks_run_in_reverse_once() -> None:
    lc = Lifecycle()
    order: list[str] = []
    lc.begin_start()
    lc.add_stop_hook("a", lambda: order.append("a"))
    lc.add_stop_hook("b", lambda: order.append("b"))
    lc.mark_running()
    assert lc.stop() == []
    assert order == ["b", "a"]
    assert lc.state is AppState.STOPPED
    assert lc.wait_stopped(0)
    assert lc.stop() == []  # idempotent
    assert order == ["b", "a"]


def test_failing_hook_does_not_stop_the_others() -> None:
    lc = Lifecycle()
    ran: list[str] = []
    lc.begin_start()
    lc.add_stop_hook("first", lambda: ran.append("first"))

    def broken() -> None:
        raise OSError("disk")

    lc.add_stop_hook("broken", broken)
    lc.mark_running()
    assert lc.stop() == ["broken"]
    assert ran == ["first"]
    assert lc.state is AppState.STOPPED


def test_failed_start_can_still_stop() -> None:
    lc = Lifecycle()
    lc.begin_start()
    lc.mark_failed()
    assert lc.stop() == []
    assert lc.state is AppState.STOPPED


def test_stop_before_start_is_a_noop_and_illegal_moves_raise() -> None:
    lc = Lifecycle()
    assert lc.stop() == []
    assert lc.state is AppState.CREATED
    with pytest.raises(InvalidTransition):
        lc.mark_running()
    with pytest.raises(RuntimeError, match="cannot add stop hook"):
        lc.add_stop_hook("x", lambda: None)
