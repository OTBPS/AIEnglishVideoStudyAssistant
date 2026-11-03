"""One place that runs, tracks and stops background work.

Every background job (fetching subtitles, calling the LLM, transcribing)
goes through TaskSupervisor.submit(). That buys, uniformly:

* a timeout on every task (default on, opt out explicitly with None);
* cancellation by handle or by id;
* exceptions captured into the task's state and logged with its name --
  never lost in a thread, never fatal to the loop;
* a safe shutdown that cancels whatever is still running and waits a
  bounded time, so quitting never hangs.

Work runs on one asyncio loop in a dedicated thread. Blocking work (a
subprocess, a synchronous SDK) is submitted with run_blocking(): it executes
in a worker thread. Python cannot kill a thread, so a timed-out blocking task
is marked TIMED_OUT and abandoned; its result is discarded if it ever arrives.
Those threads are daemon threads on purpose: asyncio's default executor uses
non-daemon workers that the interpreter joins at exit, so one stuck call
would make quitting the app hang forever.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import functools
import itertools
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .statemachine import StateMachine

log = logging.getLogger("tasks")

DEFAULT_TIMEOUT_S = 120.0


class TaskState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def finished(self) -> bool:
        return self in _FINISHED


_FINISHED = frozenset(
    {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED, TaskState.TIMED_OUT}
)
_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.RUNNING: _FINISHED,
    TaskState.SUCCEEDED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
    TaskState.TIMED_OUT: frozenset(),
}


class _UseDefault:
    """Sentinel type: 'use the supervisor's default timeout'."""


USE_DEFAULT = _UseDefault()
Timeout = float | None | _UseDefault


class SupervisorClosed(RuntimeError):
    """submit() after shutdown() began."""


class TaskTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class TaskInfo:
    id: int
    name: str
    state: TaskState
    error: str | None
    started_at: float | None
    finished_at: float | None


class TaskHandle[T]:
    def __init__(self, task_id: int, name: str, supervisor: TaskSupervisor) -> None:
        self.id = task_id
        self.name = name
        self._supervisor = supervisor
        self._sm = StateMachine(TaskState.PENDING, _TASK_TRANSITIONS)
        self._future: concurrent.futures.Future[T] = concurrent.futures.Future()
        self._error: str | None = None
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._lock = threading.Lock()
        self._asyncio_task: asyncio.Task[Any] | None = None

    @property
    def state(self) -> TaskState:
        return self._sm.state

    @property
    def future(self) -> concurrent.futures.Future[T]:
        """Await from async code with asyncio.wrap_future(handle.future)."""
        return self._future

    def info(self) -> TaskInfo:
        return TaskInfo(
            self.id, self.name, self.state, self._error, self._started_at, self._finished_at
        )

    def result(self, timeout: float | None = None) -> T:
        """Block for the outcome. Raises the task's own exception,
        concurrent.futures.CancelledError, or TaskTimeout."""
        return self._future.result(timeout)

    def cancel(self) -> bool:
        return self._supervisor.cancel(self.id)

    # --- called by the supervisor only ------------------------------------
    def _mark_running(self) -> bool:
        with self._lock:
            if self._sm.state is not TaskState.PENDING:
                return False
            self._sm.transition(TaskState.RUNNING)
            self._started_at = time.monotonic()
            return True

    def _finish(
        self, state: TaskState, *, result: Any = None, error: BaseException | None = None
    ) -> None:
        with self._lock:
            if self._sm.state.finished:
                return
            self._sm.transition(state)
            self._finished_at = time.monotonic()
            if error is not None:
                self._error = f"{type(error).__name__}: {error}"
        if state is TaskState.SUCCEEDED:
            self._future.set_result(result)
        elif state is TaskState.CANCELLED:
            self._future.cancel()
            # A cancelled-before-start future must still be marked done.
            self._future.set_running_or_notify_cancel()
        else:
            if error is None:
                raise RuntimeError(f"{state} needs an error")
            self._future.set_exception(error)


def _set_result[T](fut: asyncio.Future[T], value: T) -> None:
    if not fut.done():  # cancelled or timed out meanwhile
        fut.set_result(value)


def _set_exception(fut: asyncio.Future[Any], exc: BaseException) -> None:
    if not fut.done():
        fut.set_exception(exc)


class TaskSupervisor:
    def __init__(self, default_timeout: float | None = DEFAULT_TIMEOUT_S) -> None:
        self._default_timeout = default_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._handles: dict[int, TaskHandle[Any]] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("TaskSupervisor already started")
        ready = threading.Event()

        def run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            ready.set()
            try:
                loop.run_forever()
            finally:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

        self._thread = threading.Thread(target=run, name="tasks", daemon=True)
        self._thread.start()
        ready.wait()

    def shutdown(self, grace: float = 5.0) -> None:
        """Cancel everything still running and stop the loop. Idempotent.

        Waits at most `grace` seconds for tasks to unwind; whatever is left
        (a blocking call stuck in a thread) is abandoned, never waited on.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = [h for h in self._handles.values() if not h.state.finished]
        for handle in pending:
            self.cancel(handle.id)
        concurrent.futures.wait([h.future for h in pending], timeout=grace)
        loop, thread = self._loop, self._thread
        if loop is not None and thread is not None:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=grace)
            if thread.is_alive():
                log.warning("task loop did not stop within %.1fs; abandoning it", grace)
        log.info("task supervisor stopped (%d task(s) were cancelled)", len(pending))

    # ------------------------------------------------------------ submitting
    def submit[T](
        self, name: str, work: Callable[[], Awaitable[T]], *, timeout: Timeout = USE_DEFAULT
    ) -> TaskHandle[T]:
        """Run `work()` (a coroutine function) on the task loop."""
        loop = self._require_loop()
        limit = self._default_timeout if isinstance(timeout, _UseDefault) else timeout
        with self._lock:
            if self._closed:
                raise SupervisorClosed(f"cannot start {name!r}: shutting down")
            handle: TaskHandle[T] = TaskHandle(next(self._ids), name, self)
            self._handles[handle.id] = handle

        def schedule() -> None:
            handle._asyncio_task = loop.create_task(self._run(handle, work, limit))

        loop.call_soon_threadsafe(schedule)
        return handle

    def run_blocking[T](
        self, name: str, fn: Callable[[], T], *, timeout: Timeout = USE_DEFAULT
    ) -> TaskHandle[T]:
        """Run a blocking callable in a daemon thread, supervised like any task.

        A cancelled or timed-out call keeps running until it returns (Python
        cannot interrupt a thread); its result is discarded. Work that can stop
        early -- a download, a subprocess -- should use run_cancellable().
        """
        return self._in_thread(name, lambda _stop: fn(), timeout)

    def run_cancellable[T](
        self, name: str, fn: Callable[[threading.Event], T], *, timeout: Timeout = USE_DEFAULT
    ) -> TaskHandle[T]:
        """Like run_blocking(), but fn receives an Event that is set as soon as
        the task is cancelled, times out, or the supervisor shuts down. fn
        checks it between steps (or kills its subprocess) and returns promptly."""
        return self._in_thread(name, fn, timeout)

    def _in_thread[T](
        self, name: str, fn: Callable[[threading.Event], T], timeout: Timeout
    ) -> TaskHandle[T]:
        loop = self._require_loop()

        async def work() -> T:
            fut: asyncio.Future[T] = loop.create_future()
            stop = threading.Event()

            def deliver(outcome: Callable[[], None]) -> None:
                # RuntimeError: loop already closed, the result is abandoned.
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(outcome)

            def target() -> None:
                try:
                    value = fn(stop)
                except BaseException as exc:  # noqa: BLE001 -- forwarded to the awaiting task
                    # partial binds exc now; a lambda would see it deleted.
                    deliver(functools.partial(_set_exception, fut, exc))
                else:
                    deliver(functools.partial(_set_result, fut, value))

            threading.Thread(target=target, name=f"task:{name}", daemon=True).start()
            try:
                return await fut
            finally:
                stop.set()  # cancelled, timed out or done: tell the thread either way

        return self.submit(name, work, timeout=timeout)

    def cancel(self, task_id: int) -> bool:
        """Request cancellation. False if unknown or already finished."""
        handle = self._handles.get(task_id)
        if handle is None or handle.state.finished:
            return False
        loop = self._loop
        if loop is None:
            return False

        def do_cancel() -> None:
            if handle.state is TaskState.PENDING:
                handle._finish(TaskState.CANCELLED)
            if handle._asyncio_task is not None:
                handle._asyncio_task.cancel()

        loop.call_soon_threadsafe(do_cancel)
        return True

    def get(self, task_id: int) -> TaskHandle[Any] | None:
        return self._handles.get(task_id)

    def list(self) -> list[TaskInfo]:
        with self._lock:
            return [h.info() for h in self._handles.values()]

    def prune_finished(self) -> int:
        """Forget finished tasks so the registry cannot grow without bound."""
        with self._lock:
            done = [i for i, h in self._handles.items() if h.state.finished]
            for i in done:
                del self._handles[i]
        return len(done)

    # ------------------------------------------------------------ internals
    def _require_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("TaskSupervisor.start() was not called")
        return self._loop

    async def _run[T](
        self, handle: TaskHandle[T], work: Callable[[], Awaitable[T]], limit: float | None
    ) -> None:
        if not handle._mark_running():
            return  # cancelled before it started
        try:
            async with asyncio.timeout(limit):
                result = await work()
        except TimeoutError as exc:
            # asyncio.timeout raises TimeoutError only for *our* deadline when
            # the inner work did not raise one itself; treat both the same.
            log.warning("task %r (#%d) timed out after %ss", handle.name, handle.id, limit)
            handle._finish(TaskState.TIMED_OUT, error=TaskTimeout(str(exc) or f"{limit}s"))
        except asyncio.CancelledError:
            log.info("task %r (#%d) cancelled", handle.name, handle.id)
            handle._finish(TaskState.CANCELLED)
        except Exception as exc:
            log.exception("task %r (#%d) failed", handle.name, handle.id)
            handle._finish(TaskState.FAILED, error=exc)
        else:
            handle._finish(TaskState.SUCCEEDED, result=result)
