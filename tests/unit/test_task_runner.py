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

    Each scheduled call is given an opaque integer handle so tests can
    exercise `cancel_scheduled`-style APIs (mirrors `root.after` returning
    a handle that `root.after_cancel(handle)` consumes).
    """

    def __init__(self) -> None:
        self._pending: dict[int, Callable[[], None]] = {}
        self._next_handle = 0
        self.cancelled_handles: list[int] = []

    def __call__(self, delay_ms: int, callback: Callable[[], None]) -> int:
        del delay_ms  # unused — this fake ignores real timing entirely
        handle = self._next_handle
        self._next_handle += 1
        self._pending[handle] = callback
        return handle

    def cancel(self, handle: int) -> None:
        """Mirror `root.after_cancel(handle)` — drop a pending callback."""
        self.cancelled_handles.append(handle)
        self._pending.pop(handle, None)

    def pump(self) -> None:
        """Run every callback scheduled so far, once, synchronously."""
        callbacks = list(self._pending.values())
        self._pending = {}
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


def test_submit_from_non_owner_thread_raises() -> None:
    """`submit()` enforces the thread-affinity invariant `_pending` and
    `_tick_scheduled` rely on for lock-free access (Fix A)."""
    scheduler = _FakeScheduler()
    runner = TaskRunner(scheduler=scheduler, max_workers=1)
    captured_error: dict[str, BaseException] = {}

    def call_submit_from_other_thread() -> None:
        try:
            runner.submit(lambda: 1)
        except BaseException as exc:  # noqa: BLE001 - capture for the assertion below
            captured_error["error"] = exc

    try:
        other = threading.Thread(target=call_submit_from_other_thread)
        other.start()
        other.join(timeout=2)

        assert not other.is_alive()
        assert isinstance(captured_error.get("error"), RuntimeError)
    finally:
        runner.shutdown()


def test_shutdown_from_non_owner_thread_raises() -> None:
    """`shutdown()` enforces the same thread-affinity invariant as `submit()`
    since it also mutates `_shutting_down`/`_scheduled_handle` without a
    lock (review follow-up on Fix A: the original fix only guarded
    `submit()`)."""
    scheduler = _FakeScheduler()
    runner = TaskRunner(scheduler=scheduler, max_workers=1)
    captured_error: dict[str, BaseException] = {}

    def call_shutdown_from_other_thread() -> None:
        try:
            runner.shutdown()
        except BaseException as exc:  # noqa: BLE001 - capture for the assertion below
            captured_error["error"] = exc

    try:
        other = threading.Thread(target=call_shutdown_from_other_thread)
        other.start()
        other.join(timeout=2)

        assert not other.is_alive()
        assert isinstance(captured_error.get("error"), RuntimeError)
    finally:
        runner.shutdown()


def test_shutdown_cancels_scheduled_tick_and_stray_tick_is_noop() -> None:
    """`shutdown()` cancels the outstanding drain tick via `cancel_scheduled`,
    and even a stray tick that still fires afterward is a safe no-op
    (Fix B)."""
    scheduler = _FakeScheduler()
    runner = TaskRunner(
        scheduler=scheduler, max_workers=2, cancel_scheduled=scheduler.cancel
    )
    done = threading.Event()
    callback_fired = threading.Event()

    def slow_work() -> int:
        done.wait(timeout=2)
        return 1

    runner.submit(slow_work, on_success=lambda _r: callback_fired.set())

    # A tick was scheduled by submit(); capture it before shutdown cancels it.
    assert scheduler._pending

    done.set()
    runner.shutdown()

    assert scheduler.cancelled_handles
    # Nothing left pending in the fake scheduler after shutdown cancelled it.
    assert not scheduler._pending

    # Even if a stray tick still fires post-shutdown (e.g. no cancel_scheduled
    # was wired up, or the cancel raced), _drain()'s own guard must make it a
    # safe no-op rather than invoking callbacks against torn-down state.
    runner._drain()

    assert not callback_fired.is_set()


def test_idle_then_resubmit_still_delivers() -> None:
    """Regression test for the idle -> re-arm transition: after polling has
    gone fully idle (no tick scheduled, `_pending == 0`), a fresh `submit()`
    must still re-arm polling and deliver its result (Fix C)."""
    scheduler = _FakeScheduler()
    runner = TaskRunner(scheduler=scheduler, max_workers=2)
    first_done = threading.Event()
    second_done = threading.Event()
    second_result: dict[str, int] = {}

    try:
        runner.submit(lambda: 1, on_success=lambda _r: first_done.set())
        _pump_until(scheduler, first_done)

        # Polling must be fully idle now: nothing pending, no tick armed.
        assert runner._pending == 0
        assert runner._tick_scheduled is False
        assert not scheduler._pending

        runner.submit(
            lambda: 2,
            on_success=lambda r: (second_result.__setitem__("value", r), second_done.set()),
        )
        _pump_until(scheduler, second_done)

        assert second_result["value"] == 2
    finally:
        runner.shutdown()


def test_callback_exception_does_not_stall_remaining_queue() -> None:
    """If a consumer's `on_success`/`on_error` callback itself raises, the
    drain loop must log it, keep going, and still re-arm polling for later
    submissions (Fix D)."""
    scheduler = _FakeScheduler()
    runner = TaskRunner(scheduler=scheduler, max_workers=4)
    second_done = threading.Event()
    third_done = threading.Event()
    second_fired = threading.Event()

    def raising_on_success(_result: int) -> None:
        second_fired.set()
        raise ValueError("boom in on_success")

    try:
        runner.submit(lambda: 1, on_success=raising_on_success)
        runner.submit(lambda: 2, on_success=lambda _r: second_done.set())
        _pump_until(scheduler, second_done)

        assert second_fired.is_set()
        assert second_done.is_set()

        # Runner must not be left in a stuck/un-rearmed state: a fresh
        # submit still delivers.
        runner.submit(lambda: 3, on_success=lambda _r: third_done.set())
        _pump_until(scheduler, third_done)

        assert third_done.is_set()
    finally:
        runner.shutdown()
