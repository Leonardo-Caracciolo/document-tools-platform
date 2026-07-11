"""Main application window shell — sidebar navigation + content area.

`sdd/acrobat-tools-ui/design` §5: `MainWindow` grows a left sidebar
(fixed width, one button per `TOOL_SPECS` entry grouped by `group`) and a
right content area (`grid`, column 1 `weight=1`) that lazily instantiates
and caches one `ToolView` per tool, swapping the single active view via
`pack_forget()`/`pack()` — never destroying, so in-progress input state
is preserved across switches (spec's "Sidebar/Registry Navigation"
requirement). Owns the single shared `TaskRunner` (unchanged from Sprint
1) and injects it into every `ToolView` it creates.
"""

from __future__ import annotations

from functools import partial

import customtkinter as ctk

from app.core.concurrency.task_runner import TaskRunner
from app.ui.registry import SPEC_BY_ID, TOOL_SPECS
from app.ui.views import ToolView

WINDOW_TITLE = "Acrobat Tools"
WINDOW_SIZE = "900x600"
SIDEBAR_WIDTH = 220


class MainWindow(ctk.CTk):
    """Top-level application window: sidebar navigation + tool content area.

    Sprint 1 set up window chrome and the shared `TaskRunner`; this
    extension (design §5) adds the sidebar/content `grid` layout, builds
    one sidebar button per `TOOL_SPECS` entry (grouped by `group`), and
    lazily instantiates + caches one `ToolView` per tool on first
    selection, injecting the shared `task_runner` into each.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title(WINDOW_TITLE)
        self.geometry(WINDOW_SIZE)
        self.task_runner = TaskRunner(
            scheduler=self.after, cancel_scheduled=self.after_cancel
        )

        # col0 = sidebar (fixed width), col1 = content (expands) — grid
        # weight expansion empirically confirmed (design §9 item 6).
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=SIDEBAR_WIDTH)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)  # keep the fixed width regardless of button contents
        self.content = ctk.CTkFrame(self)
        self.content.grid(row=0, column=1, sticky="nsew")

        self._views: dict[str, ToolView] = {}
        self._current: ToolView | None = None
        self._build_sidebar()

    def _build_sidebar(self) -> None:
        """Build one group-header label + one button per `TOOL_SPECS` entry.

        A new `CTkLabel` group header is emitted whenever `group` changes
        from the previous entry — `TOOL_SPECS`'s tuple order (design §5)
        is already grouped, so a single pass suffices.
        """
        last_group: str | None = None
        for spec in TOOL_SPECS:
            if spec.group != last_group:
                ctk.CTkLabel(
                    self.sidebar, text=spec.group, font=ctk.CTkFont(weight="bold")
                ).pack(anchor="w", padx=12, pady=(12, 2))
                last_group = spec.group
            ctk.CTkButton(
                self.sidebar,
                text=spec.label,
                command=partial(self._select, spec.tool_id),
            ).pack(fill="x", padx=12, pady=2)

    def _select(self, tool_id: str) -> None:
        """Swap the content area's single active view to `tool_id`'s.

        Detaches (never destroys) the current view via `pack_forget()` so
        its in-progress input state is preserved for when the user
        returns to it, then lazily instantiates (or reuses the cached)
        target view and mounts it (spec's "Sidebar/Registry Navigation"
        requirement, design §5).
        """
        if self._current is not None:
            self._current.pack_forget()

        view = self._views.get(tool_id)
        if view is None:
            view = ToolView(self.content, self.task_runner, SPEC_BY_ID[tool_id])
            self._views[tool_id] = view

        view.pack(fill="both", expand=True)
        self._current = view

    def destroy(self) -> None:
        """Shut down the shared task runner before tearing down the window."""
        self.task_runner.shutdown()
        super().destroy()
