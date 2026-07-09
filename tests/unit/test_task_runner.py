"""Tests for `app.core.concurrency.task_runner`.

Uses a fake, immediate/synchronous scheduler instead of a real Tkinter
`after` loop, plus `threading.Event` for synchronization. No `time.sleep`
anywhere — waits are bounded, deterministic polls gated by real events.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest

from app.core.concurrency.task_runner import TaskRunner


class _FakeScheduler:
    """Captures `(delay_ms, callback)` calls instead of driving a real
    Tk mainloop or timer thread.

    `TaskRunner` calls this like `scheduler(delay_ms, callback)` expecting
    `callback` to run later on the caller's own thread. This fake never
    invokes `callback` on its own — the test drives ticks explicitly via
    `pump()`, synchronously and immediately (no delay, no sleep), which is
    what proves callbacks run on the thread that calls `pump()`.
    """

    def __init__(self) -> None:
        self._pending: list[Callable[[], None]] = []

    def __call__(self, delay_ms: int, callback: Callable[[], None]) -> None:
        del delay_ms  # unused — this fake ignores real timing entirely
        self._pending.append(callback)

    def pump(self) -> None:
        """Run every callback scheduled so far, once, synchronously."""
        callbacks, self._pending = self._pending, []
        for callback in callbacks:
            callback()


def _pump_until(
    scheduler: _FakeScheduler, done: threading.Event, timeout: float = 2.0
) -> None:
    """Pump the fake scheduler until `done` is set.

    No `time.sleep` — a tight poll bounded by a wall-clock deadline so a
    real bug (deadlock, dropped callback) fails fast instead of hanging.
    """
    deadline = time.monotonic() + timeout
    while not done.is_set():
        scheduler.pump()
        if time.monotonic() > deadline:
            raise AssertionError(
                "Timed out waiting for TaskRunner to drain the queue."
            )


def test_submit_returns_before_work_completes() -> None:
    scheduler = _FakeScheduler()
    runner = TaskRunner(scheduler=scheduler, max_workers=2)
    release = threading.Event()

    def slow_work() -> int:
        release.wait(timeout=2)
        return 42

    try:
        start = time.monotonic()
        runner.submit(slow_work, on_success=lambda _result: None)
        elapsed = time.monotonic() - start

        # If submit() ran `slow_work` synchronously on the caller thread,
        # this line would not be reached until `release` is set (up to the
        # 2s timeout inside `slow_work`). Returning near-instantly proves
        # the work was handed off to a background thread instead.
        assert elapsed < 0.5
        assert not release.is_set()
    finally:
        release.set()
        runner.shutdown()


def test_on_success_fires_on_caller_thread_not_worker_thread() -> None:
    scheduler = _FakeScheduler()
    runner = TaskRunner(scheduler=scheduler, max_workers=2)
    done = threading.Event()
    caller_thread = threading.current_thread()
    worker_thread: dict[str, threading.Thread] = {}
    callback_thread: dict[str, threading.Thread] = {}
    result_value: dict[str, int] = {}

    def work() -> int:
        worker_thread["thread"] = threading.current_thread()
        return 42

    def on_success(result: int) -> None:
        callback_thread["thread"] = threading.current_thread()
        result_value["value"] = result
        done.set()

    try:
        runner.submit(work, on_success=on_success)
        _pump_until(scheduler, done)

        assert result_value["value"] == 42
        assert callback_thread["thread"] is caller_thread
        assert worker_thread["thread"] is not caller_thread
        assert callback_thread["thread"] is not worker_thread["thread"]
    finally:
        runner.shutdown()


def test_worker_exception_routes_to_on_error_not_swallowed() -> None:
    scheduler = _FakeScheduler()
    runner = TaskRunner(scheduler=scheduler, max_workers=2)
    done = threading.Event()
    caller_thread = threading.current_thread()
    error_thread: dict[str, threading.Thread] = {}
    captured_error: dict[str, BaseException] = {}
    success_called = threading.Event()

    boom = ValueError("worker boom")

    def failing_work() -> int:
        raise boom

    def on_success(_result: int) -> None:
        success_called.set()

    def on_error(exc: BaseException) -> None:
        error_thread["thread"] = threading.current_thread()
        captured_error["error"] = exc
        done.set()

    try:
        runner.submit(failing_work, on_success=on_success, on_error=on_error)
        _pump_until(scheduler, done)

        assert captured_error["error"] is boom
        assert error_thread["thread"] is caller_thread
        assert not success_called.is_set()
    finally:
        runner.shutdown()


def test_multiple_submissions_all_drain() -> None:
    scheduler = _FakeScheduler()
    runner = TaskRunner(scheduler=scheduler, max_workers=4)
    done = threading.Event()
    results: list[int] = []
    lock = threading.Lock()
    expected = 5

    def make_work(n: int) -> Callable[[], int]:
        def _work() -> int:
            return n

        return _work

    def on_success(result: int) -> None:
        with lock:
            results.append(result)
            if len(results) == expected:
                done.set()

    try:
        for i in range(expected):
            runner.submit(make_work(i), on_success=on_success)

        _pump_until(scheduler, done)

        assert sorted(results) == list(range(expected))
    finally:
        runner.shutdown()


def test_shutdown_rejects_new_submissions() -> None:
    scheduler = _FakeScheduler()
    runner = TaskRunner(scheduler=scheduler, max_workers=1)

    runner.shutdown()

    with pytest.raises(RuntimeError):
        runner.submit(lambda: 1)
