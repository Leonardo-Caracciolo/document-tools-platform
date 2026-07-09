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
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue
from typing import Any

#: How often (ms) the runner asks `scheduler` to re-check the queue while
#: work is still outstanding. Only used as the `delay_ms` argument passed
#: to `scheduler` — this module never sleeps or times anything itself.
POLL_MS = 50

_Callback = Callable[[Any], None]
_QueueItem = tuple[_Callback | None, Any]


class TaskRunner:
    """Runs callables off the UI thread and delivers results back on it."""

    def __init__(
        self,
        scheduler: Callable[[int, Callable[[], None]], object],
        max_workers: int = 4,
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
        """
        self._scheduler = scheduler
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._queue: Queue[_QueueItem] = Queue()
        self._pending = 0
        self._tick_scheduled = False
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

        Raises:
            RuntimeError: if called after `shutdown()`.
        """
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

        Waits for already-running work to finish before returning. No
        further queue-drain ticks are scheduled after this call.
        """
        self._shutting_down = True
        self._executor.shutdown(wait=True)

    def _ensure_tick_scheduled(self) -> None:
        """Ask `scheduler` for one future `_drain` tick, if none is pending."""
        if self._shutting_down or self._tick_scheduled:
            return
        self._tick_scheduled = True
        self._scheduler(POLL_MS, self._drain)

    def _drain(self) -> None:
        """Deliver every completed result currently queued.

        Runs on whichever thread `scheduler` invokes it on (the UI thread
        in production). If work is still outstanding after draining, asks
        `scheduler` for another tick; otherwise polling stops until the
        next `submit()` call restarts it.
        """
        self._tick_scheduled = False

        while True:
            try:
                callback, payload = self._queue.get_nowait()
            except Empty:
                break
            self._pending -= 1
            if callback is not None:
                callback(payload)

        if self._pending > 0 and not self._shutting_down:
            self._ensure_tick_scheduled()
