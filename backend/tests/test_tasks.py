from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from collections.abc import Iterator

import pytest

from videotutor.core.tasks import (
    SupervisorClosed,
    TaskState,
    TaskSupervisor,
    TaskTimeout,
)


@pytest.fixture
def sup() -> Iterator[TaskSupervisor]:
    s = TaskSupervisor(default_timeout=5)
    s.start()
    yield s
    s.shutdown(grace=2)


def _wait_state(handle: object, state: TaskState, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if handle.state is state:  # type: ignore[attr-defined]
            return
        time.sleep(0.01)
    raise AssertionError(f"state stayed {handle.state}")  # type: ignore[attr-defined]


def test_success_returns_result(sup: TaskSupervisor) -> None:
    async def work() -> int:
        await asyncio.sleep(0)
        return 42

    h = sup.submit("answer", work)
    assert h.result(timeout=2) == 42
    assert h.state is TaskState.SUCCEEDED
    info = h.info()
    assert info.name == "answer" and info.error is None
    assert info.started_at is not None and info.finished_at is not None


def test_failure_is_captured_not_fatal(sup: TaskSupervisor) -> None:
    async def boom() -> None:
        raise ValueError("bad input")

    h = sup.submit("boom", boom)
    with pytest.raises(ValueError, match="bad input"):
        h.result(timeout=2)
    assert h.state is TaskState.FAILED
    assert h.info().error == "ValueError: bad input"

    async def ok() -> str:
        return "still alive"

    assert sup.submit("after", ok).result(timeout=2) == "still alive"


def test_timeout(sup: TaskSupervisor) -> None:
    async def slow() -> None:
        await asyncio.sleep(10)

    h = sup.submit("slow", slow, timeout=0.05)
    with pytest.raises(TaskTimeout):
        h.result(timeout=2)
    assert h.state is TaskState.TIMED_OUT


def test_no_timeout_when_disabled(sup: TaskSupervisor) -> None:
    async def short() -> str:
        await asyncio.sleep(0.05)
        return "done"

    assert sup.submit("unbounded", short, timeout=None).result(timeout=2) == "done"


def test_cancel_running_task(sup: TaskSupervisor) -> None:
    started = threading.Event()

    async def forever() -> None:
        started.set()
        await asyncio.sleep(10)

    h = sup.submit("forever", forever)
    assert started.wait(2)
    assert h.cancel()
    with pytest.raises(concurrent.futures.CancelledError):
        h.result(timeout=2)
    assert h.state is TaskState.CANCELLED
    assert not h.cancel(), "cancelling a finished task reports False"


def test_cancel_unknown_id(sup: TaskSupervisor) -> None:
    assert not sup.cancel(999)
    assert sup.get(999) is None


def test_blocking_work_runs_in_a_thread(sup: TaskSupervisor) -> None:
    def blocking() -> str:
        return threading.current_thread().name

    name = sup.run_blocking("io", blocking).result(timeout=2)
    assert name == "task:io"


def test_blocking_failure_and_timeout(sup: TaskSupervisor) -> None:
    def fails() -> None:
        raise OSError("disk gone")

    h = sup.run_blocking("fails", fails)
    with pytest.raises(OSError, match="disk gone"):
        h.result(timeout=2)

    release = threading.Event()
    stuck = sup.run_blocking("stuck", lambda: release.wait(5), timeout=0.05)
    with pytest.raises(TaskTimeout):
        stuck.result(timeout=2)
    release.set()  # the abandoned thread finishing late must not break anything


def test_list_and_prune(sup: TaskSupervisor) -> None:
    async def ok() -> None:
        return None

    h = sup.submit("one", ok)
    h.result(timeout=2)
    assert [i.name for i in sup.list()] == ["one"]
    assert sup.prune_finished() == 1
    assert sup.list() == []


def test_shutdown_cancels_running_and_refuses_new_work() -> None:
    sup = TaskSupervisor()
    sup.start()
    started = threading.Event()

    async def forever() -> None:
        started.set()
        await asyncio.sleep(10)

    h = sup.submit("forever", forever)
    assert started.wait(2)
    t0 = time.monotonic()
    sup.shutdown(grace=2)
    assert time.monotonic() - t0 < 2
    assert h.state is TaskState.CANCELLED
    with pytest.raises(SupervisorClosed):
        sup.submit("late", forever)
    sup.shutdown()  # idempotent


def test_shutdown_does_not_wait_for_stuck_threads() -> None:
    sup = TaskSupervisor()
    sup.start()
    release = threading.Event()
    sup.run_blocking("stuck", lambda: release.wait(10))
    t0 = time.monotonic()
    sup.shutdown(grace=0.3)
    assert time.monotonic() - t0 < 2
    release.set()


def test_misuse_is_reported() -> None:
    sup = TaskSupervisor()

    async def ok() -> None:
        return None

    with pytest.raises(RuntimeError, match="start"):
        sup.submit("x", ok)
    sup.start()
    with pytest.raises(RuntimeError, match="already started"):
        sup.start()
    sup.shutdown()
