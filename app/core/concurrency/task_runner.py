"""Background task execution off the UI thread, per SSD.md §5.2.

`TaskRunner` submits callables to a shared `ThreadPoolExecutor` so the
calling (UI) thread never blocks. Each completed unit of work — success or
exception — is pushed onto a thread-safe `queue.Queue`; nothing is ever
delivered directly from a worker thread. Draining that queue and invoking
`on_success`/`on_error` happens exclusively through the caller-supplied
`scheduler` (an `after`-like callable, e.g. `root.after`), so callbacks
always run on whichever thread drives `scheduler` — the UI thread in
production. No worker thread ever touches a UI widget directly: this is
the exact §5.2/§10 contract every future Service (PDF/OCR/Export/Audit)
inherits starting Sprint 1.

Usage::

    runner = TaskRunner(scheduler=root.after, cancel_scheduled=root.after_cancel)
    runner.submit(
        load_document,
        path,
        on_success=lambda doc: label.configure(text=doc.title),
        on_error=lambda exc: messagebox.showerror("Load failed", str(exc)),
    )
    # ... later, e.g. on window close:
    runner.shutdown()
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue
from typing import Any

from app.infrastructure.logger import get_logger

#: How often (ms) the runner asks `scheduler` to re-check the queue while
#: work is still outstanding. Only used as the `delay_ms` argument passed
#: to `scheduler` — this module never sleeps or times anything itself.
POLL_MS = 50

_Callback = Callable[[Any], None]
_QueueItem = tuple[_Callback | None, Any]

_logger = get_logger(__name__)


class TaskRunner:
    """Runs callables off the UI thread and delivers results back on it.

    Thread affinity invariant: `submit()`, `shutdown()`, and the
    queue-drain tick (`_drain()`, invoked by `scheduler`) all mutate shared
    state (`_pending`, `_tick_scheduled`, `_scheduled_handle`,
    `_shutting_down`) without a lock. This is only race-free because all
    three are required to run on the same thread — the thread that
    constructed this `TaskRunner` and that owns `scheduler` (the UI/main
    thread in production). `submit()` and `shutdown()` both enforce this at
    runtime by comparing `threading.get_ident()` against the thread that
    called `__init__`.
    """

    def __init__(
        self,
        scheduler: Callable[[int, Callable[[], None]], object],
        max_workers: int = 4,
        cancel_scheduled: Callable[[object], None] | None = None,
    ) -> None:
        """Create a runner backed by a shared `ThreadPoolExecutor`.

        Args:
            scheduler: An `after`-like callable — `scheduler(delay_ms, fn)`
                arranges for `fn` to run later on the caller's own thread
                (e.g. `tkinter.Misc.after`). `TaskRunner` never calls a
                worker's result callback directly; it always goes through
                `scheduler`, so callbacks land on the same thread that owns
                `scheduler`.
            max_workers: Size of the shared background thread pool.
            cancel_scheduled: Optional `after_cancel`-like callable —
                `cancel_scheduled(handle)` cancels a previously scheduled
                `scheduler` call (e.g. `tkinter.Misc.after_cancel`). If
                provided, `shutdown()` uses it to cancel any outstanding
                drain tick instead of letting it fire and no-op later.

        The thread that calls `__init__` becomes this runner's owner
        thread: `submit()` must always be called from that same thread
        (see class docstring).
        """
        self._owner_thread_id = threading.get_ident()
        self._scheduler = scheduler
        self._cancel_scheduled = cancel_scheduled
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._queue: Queue[_QueueItem] = Queue()
        self._pending = 0
        self._tick_scheduled = False
        self._scheduled_handle: object | None = None
        self._shutting_down = False

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_success: _Callback | None = None,
        on_error: _Callback | None = None,
        **kwargs: Any,
    ) -> None:
        """Run `fn(*args, **kwargs)` off the UI thread.

        Returns immediately — this call never blocks the calling thread on
        `fn`'s execution. Once `fn` finishes, exactly one of
        `on_success(result)` or `on_error(exception)` is invoked later, on
        the thread that drives `scheduler`, never on the worker thread that
        ran `fn`. A raised exception is always captured and routed to
        `on_error` — it is never silently swallowed.

        Must be called from the same thread that constructed this
        `TaskRunner` (the thread that owns `scheduler`) — see the class
        docstring for why. Calling it from any other thread raises
        `RuntimeError`.

        Raises:
            RuntimeError: if called from a thread other than the owner
                thread, or if called after `shutdown()`.
        """
        caller_thread_id = threading.get_ident()
        if caller_thread_id != self._owner_thread_id:
            raise RuntimeError(
                "TaskRunner.submit() must be called from the thread that "
                f"owns the scheduler (owner thread id={self._owner_thread_id}), "
                f"but was called from thread id={caller_thread_id}."
            )

        if self._shutting_down:
            raise RuntimeError("TaskRunner.submit() called after shutdown().")

        self._pending += 1

        def _run() -> None:
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - every worker failure must reach on_error, never be swallowed here
                self._queue.put((on_error, exc))
            else:
                self._queue.put((on_success, result))

        self._executor.submit(_run)
        self._ensure_tick_scheduled()

    def shutdown(self) -> None:
        """Stop accepting new work and stop the shared thread pool.

        Waits for already-running work to finish before returning. Cancels
        any outstanding drain tick (if `cancel_scheduled` was provided), so
        no stray tick can fire against torn-down UI state. No further
        queue-drain ticks are scheduled after this call; even without a
        `cancel_scheduled`, `_drain()` itself no-ops once shutdown has
        started.

        Must be called from the same thread that constructed this
        `TaskRunner`, for the same reason as `submit()` — it mutates
        `_shutting_down` and `_scheduled_handle`, the same unlocked shared
        state `_drain()`/`_ensure_tick_scheduled()` touch.

        Raises:
            RuntimeError: if called from a thread other than the owner
                thread.
        """
        caller_thread_id = threading.get_ident()
        if caller_thread_id != self._owner_thread_id:
            raise RuntimeError(
                "TaskRunner.shutdown() must be called from the thread that "
                f"owns the scheduler (owner thread id={self._owner_thread_id}), "
                f"but was called from thread id={caller_thread_id}."
            )

        self._shutting_down = True

        if self._scheduled_handle is not None and self._cancel_scheduled is not None:
            self._cancel_scheduled(self._scheduled_handle)
            self._scheduled_handle = None

        self._executor.shutdown(wait=True)

    def _ensure_tick_scheduled(self) -> None:
        """Ask `scheduler` for one future `_drain` tick, if none is pending."""
        if self._shutting_down or self._tick_scheduled:
            return
        self._tick_scheduled = True
        self._scheduled_handle = self._scheduler(POLL_MS, self._drain)

    def _drain(self) -> None:
        """Deliver every completed result currently queued.

        Runs on whichever thread `scheduler` invokes it on (the UI thread
        in production). If work is still outstanding after draining, asks
        `scheduler` for another tick; otherwise polling stops until the
        next `submit()` call restarts it.

        If a callback (`on_success`/`on_error`) raises, the exception is
        logged and swallowed so the rest of the queue still drains and the
        trailing re-arm check still runs — one misbehaving consumer must
        never stall polling for every other Service sharing this runner.
        """
        if self._shutting_down:
            return

        self._tick_scheduled = False
        self._scheduled_handle = None

        while True:
            try:
                callback, payload = self._queue.get_nowait()
            except Empty:
                break
            self._pending -= 1
            if callback is not None:
                try:
                    callback(payload)
                except Exception:  # noqa: BLE001 - a consumer callback failure must never abort the drain loop
                    _logger.exception(
                        "TaskRunner: callback %r raised while draining queue; "
                        "continuing with remaining items.",
                        callback,
                    )

        if self._pending > 0 and not self._shutting_down:
            self._ensure_tick_scheduled()
