"""Shared Windows process-enumeration helpers for integration tests.

Extracted from near-identical copies in `test_com_word_provider.py` and
`test_tesseract_ocr_provider.py` (both needed a "no orphaned external
process after a forced timeout" regression check — one for `WINWORD.EXE`,
one for `tesseract.exe`) so the `tasklist`-parsing logic and the
poll-until-deadline pattern have exactly one implementation to keep in
sync if Windows `tasklist` output format or timing tolerances ever need
adjusting. Prefixed with `_` (not a `test_*` module) so pytest does not
collect it as a test file.
"""

from __future__ import annotations

import csv
import io
import subprocess
import time


def running_pids_for_image(image_name: str) -> set[int]:
    """Return the PIDs of every currently-running process named `image_name`.

    E.g. `running_pids_for_image("WINWORD.EXE")` or `("tesseract.exe")`.
    """
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
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


def assert_no_orphaned_process(
    image_name: str, pids_before: set[int], timeout_seconds: float = 10.0
) -> None:
    """Assert no NEW `image_name` process survives `timeout_seconds` after a probe.

    Polls rather than checking once immediately: process teardown
    (`taskkill`/`terminate()`/`kill()`) is not necessarily instantaneous,
    so a single immediate check can flag a process that is mid-teardown,
    not genuinely orphaned.
    """
    deadline = time.monotonic() + timeout_seconds
    orphaned = running_pids_for_image(image_name) - pids_before
    while orphaned and time.monotonic() < deadline:
        time.sleep(0.5)
        orphaned = running_pids_for_image(image_name) - pids_before

    assert not orphaned, (
        f"Orphaned {image_name} process(es) left running after timeout: {orphaned}"
    )
