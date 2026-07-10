"""`ComWordProvider` — Word COM automation via a two-phase queue protocol, per SSD §5.1/§6.2.

`ComWordProvider` implements `DocumentConverterProvider` (see
`app.core.providers.document_converter_provider`) by driving Microsoft
Word COM automation, isolated in a `multiprocessing.Process` bounded by a
~60s deadline so a hung/unresponsive Word instance can never block the
caller indefinitely.

Empirically confirmed during design (`sdd/word-to-pdf-provider/design`,
"Empirical status" / "Decision 1"): a naive "just `terminate()` the child
process on timeout" approach LEAKS an orphaned `WINWORD.EXE` process —
killing the Python child does not tear down the out-of-process COM server
it activated (confirmed via `tasklist` before/after, not theoretical).

The fix, also empirically validated, is a mandatory TWO-PHASE queue
protocol:
    1. The child snapshots running `WINWORD.EXE` PIDs BEFORE calling
       `Dispatch("Word.Application")`.
    2. Right after `Dispatch()` succeeds, the child re-snapshots and diffs
       to find Word's own new PID, then puts `("word_pid", pid_or_None)`
       on the queue IMMEDIATELY — before opening/converting the document,
       which is the call that can hang.
    3. The parent POLLS the queue in a loop (never a single blocking
       `join(timeout=60)`), so it can receive the early `word_pid`
       message and keep waiting for the terminal `ok`/`error` message up
       to one overall 60s deadline from process start.
    4. On deadline exceeded without a terminal message, the parent kills
       the child process AND, if a `word_pid` was captured, explicitly
       `taskkill /F /PID <word_pid>`s it — this explicit Word-PID kill is
       the confirmed fix, not optional cleanup.

Do NOT simplify this back to a single-message queue / single blocking
`join` — that version is confirmed to leak `WINWORD.EXE`.

`convertir` raises `ConversorNoDisponibleError` directly when the
provider is unavailable — the one accepted coupling where a provider
imports `app.core.exceptions` (see design's Exception Mapping table).
`TimeoutError` and raw conversion failures (`RuntimeError`) are left to
propagate uncaught: `ExportService`'s `_translate_provider_errors`
boundary (PR3) maps both to `ConversionFallidaError` — this module does
not know about that domain exception.
"""

from __future__ import annotations

import contextlib
import csv
import importlib.util
import io
import multiprocessing
import platform
import subprocess
import threading
import time
import winreg
from pathlib import Path
from queue import Empty

import win32com.client

from app.core.exceptions import ConversorNoDisponibleError
from app.infrastructure.logger import get_logger

#: Serializes `convertir()` calls process-wide. Required by the PID
#: snapshot-diff protocol in `_run_convert`: if two conversions raced,
#: `_snapshot_word_pids()` (system-wide, not scoped to a single child)
#: could see 0 or 2+ new PIDs and silently disarm the orphan-kill safety
#: net for both. `pywin32`/COM automation is single-user-desktop-scale
#: (SSD §5.2 — one TaskRunner-driven operation at a time from the UI
#: anyway), so serializing here costs nothing in practice and closes a
#: real correctness gap found in review.
_CONVERSION_LOCK = threading.Lock()

#: Overall wall-clock deadline for one conversion — spec's "Conversion
#: Failure and Timeout Containment" requirement: "~60 seconds", folded
#: into `ConversionFallidaError` by `ExportService`, not a dedicated
#: timeout exception.
_TIMEOUT_SECONDS = 60.0

#: How often the parent drains the queue while waiting. Small enough that
#: the early `word_pid` message is picked up promptly; large enough not
#: to busy-loop.
_POLL_INTERVAL_SECONDS = 0.25

#: How long to wait for the child to exit cleanly after `terminate()`/
#: `kill()` before giving up and moving on.
_PROCESS_JOIN_TIMEOUT_SECONDS = 5.0

#: `wdFormatPDF` — Word's `SaveAs` `FileFormat` constant for PDF output.
_WD_FORMAT_PDF = 17

#: `wdDoNotSaveChanges` — passed to `Document.Close()` so a conversion
#: never prompts to save/mutates the source `.docx`.
_WD_DO_NOT_SAVE_CHANGES = 0


def _snapshot_word_pids() -> set[int]:
    """Return the PIDs of every currently-running `WINWORD.EXE` process.

    Shells out to `tasklist` rather than adding `psutil` as a new
    dependency. Parsed as CSV so an unexpected/empty `tasklist` output
    (e.g. "INFO: No tasks are running...") fails safe to an empty set
    instead of raising.
    """
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids: set[int] = set()
    for row in csv.reader(io.StringIO(result.stdout)):
        if len(row) < 2:
            continue
        try:
            pids.add(int(row[1]))
        except ValueError:
            continue
    return pids


def _run_convert(source: Path, output: Path, queue: multiprocessing.Queue) -> None:
    """`multiprocessing.Process` target — runs the actual conversion.

    Must stay a module-level function (not a closure/method): Windows'
    `multiprocessing` always uses the `spawn` start method, which pickles
    a reference to the target by module + qualified name — closures and
    bound methods are not picklable.

    Implements the mandatory two-phase queue protocol described in the
    module docstring: reports the spawned Word PID back to the parent
    IMMEDIATELY (phase 1, before the call that can hang), then the
    terminal outcome once conversion finishes (phase 2). `Word.Quit()` is
    always attempted in a `finally`, on both success and failure, so an
    ordinary conversion error doesn't itself leak an orphaned Word
    instance — the parent's PID-kill on timeout is the authoritative
    safety net for the case where this process itself has to be killed.
    """
    before = _snapshot_word_pids()
    try:
        word = win32com.client.Dispatch("Word.Application")
    except Exception as exc:  # noqa: BLE001 - reported to the parent via the queue, never raised in this process
        queue.put(("error", str(exc)))
        return

    after = _snapshot_word_pids()
    new_pids = after - before
    word_pid = next(iter(new_pids)) if len(new_pids) == 1 else None
    # Phase 1: report the PID BEFORE opening/converting the document —
    # that call is the one that can hang, so the parent must already know
    # this PID before it happens.
    queue.put(("word_pid", word_pid))

    conversion_error: str | None = None
    try:
        word.Visible = False
        # Suppress modal format-compatibility/repair prompts: an invisible
        # dialog waiting for input would otherwise stall Open/SaveAs for
        # the full timeout instead of completing normally.
        word.DisplayAlerts = 0  # wdAlertsNone
        document = word.Documents.Open(str(source))
        try:
            document.SaveAs(str(output), FileFormat=_WD_FORMAT_PDF)
        finally:
            document.Close(_WD_DO_NOT_SAVE_CHANGES)
    except Exception as exc:  # noqa: BLE001 - reported to the parent via the queue, never raised in this process
        conversion_error = str(exc)
    finally:
        # Best-effort cleanup only, deliberately swallowing any failure here:
        # the parent's captured word_pid + taskkill (on timeout) is the
        # authoritative safety net, not this call.
        with contextlib.suppress(Exception):
            word.Quit()

    # Phase 2: terminal outcome.
    if conversion_error is not None:
        queue.put(("error", conversion_error))
    else:
        queue.put(("ok", None))


class ComWordProvider:
    """`DocumentConverterProvider` backed by Word COM automation.

    Runs each conversion in an isolated `multiprocessing.Process` bounded
    by `_TIMEOUT_SECONDS`, communicating via the two-phase queue protocol
    described in this module's docstring so a timed-out conversion never
    leaves an orphaned `WINWORD.EXE` process behind.
    """

    def __init__(self) -> None:
        self._log = get_logger(__name__)

    def esta_disponible(self) -> tuple[bool, str]:
        """Cheap probe: platform + import + registry checks only.

        Deliberately does NOT call `Dispatch("Word.Application")` — doing
        so would launch a real `WINWORD.EXE` process just to answer this
        question. Informational only: per spec's "Provider Unavailable"
        requirement, unavailability is detected at `convertir` invocation
        time, never gated at construction.
        """
        if platform.system() != "Windows":
            return False, "COM/Word automation requires Windows."
        if importlib.util.find_spec("win32com") is None:
            return False, "pywin32 (win32com) is not installed."
        try:
            key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application")
        except FileNotFoundError:
            return False, "Word is not registered (Word.Application ProgID not found)."
        else:
            winreg.CloseKey(key)
        return True, "COM/Word available"

    def convertir(self, source: Path, output: Path) -> Path:
        """Convert `source` (`.docx`) to a PDF at `output` via Word COM.

        Raises:
            ConversorNoDisponibleError: `esta_disponible()` reports this
                provider cannot run right now.
            TimeoutError: conversion did not complete within
                `_TIMEOUT_SECONDS`. Any orphaned Word process captured via
                the two-phase queue protocol is killed before this is
                raised — see module docstring.
            RuntimeError: the child process reported a conversion failure
                (raw COM/pywin32 error).

        `TimeoutError`/`RuntimeError` are intentionally left uncaught
        here — `ExportService`'s `_translate_provider_errors` boundary
        (PR3) maps both to `ConversionFallidaError`; this provider does
        not import or raise that domain exception.
        """
        available, reason = self.esta_disponible()
        if not available:
            self._log.warning("ComWordProvider unavailable (%s): %s", source.name, reason)
            raise ConversorNoDisponibleError(reason)

        source = source.resolve()
        output = output.resolve()

        self._log.info("ComWordProvider convert start: %s", source.name)

        # Serialized: see _CONVERSION_LOCK docstring — the PID snapshot-diff
        # protocol below is only unambiguous for one in-flight conversion.
        with _CONVERSION_LOCK:
            queue: multiprocessing.Queue = multiprocessing.Queue()
            process = multiprocessing.Process(target=_run_convert, args=(source, output, queue))
            process.start()

            word_pid: int | None = None
            terminal: tuple[str, object] | None = None
            deadline = time.monotonic() + _TIMEOUT_SECONDS

            while terminal is None and time.monotonic() < deadline:
                remaining = max(0.0, min(_POLL_INTERVAL_SECONDS, deadline - time.monotonic()))
                try:
                    tag, payload = queue.get(timeout=remaining)
                except Empty:
                    continue
                if tag == "word_pid":
                    word_pid = payload
                else:
                    terminal = (tag, payload)

            if terminal is None:
                # Deadline just passed on the polling loop's clock, but the
                # child may have enqueued its terminal message a moment
                # earlier — drain once more, non-blocking, before concluding
                # this was a real timeout (avoids a spurious TimeoutError for
                # a conversion that actually finished right at the boundary).
                try:
                    tag, payload = queue.get_nowait()
                    if tag != "word_pid":
                        terminal = (tag, payload)
                except Empty:
                    pass

            if terminal is None:
                self._kill_process_and_word(process, word_pid, source)
                raise TimeoutError(
                    f"Conversion of {source.name!r} did not complete within "
                    f"{_TIMEOUT_SECONDS:.0f}s."
                )

            process.join(timeout=_PROCESS_JOIN_TIMEOUT_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(timeout=_PROCESS_JOIN_TIMEOUT_SECONDS)
            queue.close()
            queue.join_thread()

        tag, payload = terminal
        if tag == "error":
            self._log.warning("ComWordProvider convert failed (%s): %s", source.name, payload)
            raise RuntimeError(str(payload))

        self._log.info("ComWordProvider convert ok: %s -> %s", source.name, output.name)
        return output

    def _kill_process_and_word(
        self, process: multiprocessing.Process, word_pid: int | None, source: Path
    ) -> None:
        """Kill the child process and, if captured, the orphaned Word PID.

        This explicit `taskkill /F /PID` on the Word process (in addition
        to killing the child) is the confirmed fix for the orphan-
        `WINWORD.EXE` bug found during design's empirical validation —
        killing the Python child alone does NOT terminate the
        out-of-process COM server it activated. See module docstring.
        """
        self._log.warning("ComWordProvider timed out converting %s", source.name)
        process.terminate()
        process.join(timeout=_PROCESS_JOIN_TIMEOUT_SECONDS)
        if process.is_alive():
            process.kill()
            process.join(timeout=_PROCESS_JOIN_TIMEOUT_SECONDS)
        if word_pid is not None:
            # Best-effort: this is the confirmed fix for the orphan-Word
            # bug, but it must never itself raise in place of the
            # TimeoutError the caller is about to see — a missing/blocked
            # taskkill.exe (e.g. a locked-down deployment) shouldn't turn
            # a timeout into an unrelated, undocumented exception.
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(word_pid)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError:
                self._log.warning(
                    "ComWordProvider could not taskkill orphaned Word PID %d for %s",
                    word_pid,
                    source.name,
                )
