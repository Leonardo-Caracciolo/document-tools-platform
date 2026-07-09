"""Main application window shell (placement only).

`MainWindow` is the top-level `customtkinter` window that Sprint 1+ tool
views attach to. It owns window chrome (title, geometry) and the shared
`TaskRunner` wiring point so every future Service can dispatch background
work through one instance — it carries no PDF/OCR/converter logic itself.
"""

from __future__ import annotations

import customtkinter as ctk

from app.core.concurrency.task_runner import TaskRunner

WINDOW_TITLE = "Acrobat Tools"
WINDOW_SIZE = "900x600"


class MainWindow(ctk.CTk):
    """Top-level application window (placement only, no tool logic).

    Sprint 1+ views/widgets attach under this window; this class only
    sets up window chrome and the shared `TaskRunner` used to keep the UI
    thread non-blocking per SSD.md §5.2.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title(WINDOW_TITLE)
        self.geometry(WINDOW_SIZE)
        self.task_runner = TaskRunner(
            scheduler=self.after, cancel_scheduled=self.after_cancel
        )

    def destroy(self) -> None:
        """Shut down the shared task runner before tearing down the window."""
        self.task_runner.shutdown()
        super().destroy()
