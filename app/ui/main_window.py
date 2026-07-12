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
WINDOW_SIZE = "1050x680"
WINDOW_MIN_SIZE = (900, 600)
SIDEBAR_WIDTH = 220

#: Sidebar button visual states (cosmetic only, design polish pass). Idle
#: buttons read as flat list items; the active button is filled with the
#: theme's own default button color (captured at build time in
#: `_build_sidebar`, so it always matches whatever color theme is active)
#: so exactly one sidebar entry ever looks "selected" at a time.
_SIDEBAR_IDLE_FG_COLOR = "transparent"
_SIDEBAR_IDLE_HOVER_COLOR = ("gray81", "gray21")


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
        self.minsize(*WINDOW_MIN_SIZE)
        self.task_runner = TaskRunner(
            scheduler=self.after, cancel_scheduled=self.after_cancel
        )

        # col0 = sidebar (fixed width), col1 = content (expands) — grid
        # weight expansion empirically confirmed (design §9 item 6).
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # CTkScrollableFrame (not CTkFrame): once enough tools/groups are
        # registered to exceed the window's vertical space (confirmed with
        # 13 tools at the 900x600 minsize), a plain CTkFrame has no way to
        # reach the buttons that overflow below the fold. Its own `width=`
        # already holds fixed regardless of contents (empirically confirmed
        # — unlike CTkFrame it does not need/support `grid_propagate`).
        self.sidebar = ctk.CTkScrollableFrame(self, width=SIDEBAR_WIDTH)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.content = ctk.CTkFrame(self)
        self.content.grid(row=0, column=1, sticky="nsew")

        self._views: dict[str, ToolView] = {}
        self._current: ToolView | None = None
        self._sidebar_buttons: dict[str, ctk.CTkButton] = {}
        self._active_tool_id: str | None = None
        self._active_fg_color: str | tuple[str, str] | None = None
        self._active_hover_color: str | tuple[str, str] | None = None
        self._build_sidebar()

    def _build_sidebar(self) -> None:
        """Build one group-header label + one button per `TOOL_SPECS` entry.

        A new `CTkLabel` group header is emitted whenever `group` changes
        from the previous entry — `TOOL_SPECS`'s tuple order (design §5)
        is already grouped, so a single pass suffices. Each button is
        recorded in `_sidebar_buttons` (keyed by `tool_id`) so `_select`
        can toggle its idle/active visual state; the theme's own default
        button colors are captured once (from the first button) as the
        "active" style before every button is switched to the flat idle
        style.
        """
        last_group: str | None = None
        for spec in TOOL_SPECS:
            if spec.group != last_group:
                ctk.CTkLabel(
                    self.sidebar, text=spec.group, font=ctk.CTkFont(weight="bold")
                ).pack(anchor="w", padx=16, pady=(16, 4))
                last_group = spec.group
            button = ctk.CTkButton(
                self.sidebar,
                text=f"{spec.icon}  {spec.label}" if spec.icon else spec.label,
                anchor="w",
                command=partial(self._select, spec.tool_id),
            )
            if self._active_fg_color is None:
                self._active_fg_color = button.cget("fg_color")
                self._active_hover_color = button.cget("hover_color")
            self._set_idle(button)
            button.pack(fill="x", padx=14, pady=4)
            self._sidebar_buttons[spec.tool_id] = button

    @staticmethod
    def _set_idle(button: ctk.CTkButton) -> None:
        """Paint `button` with the one shared idle sidebar style.

        Single source of truth for the idle look — both `_build_sidebar`
        (every button starts idle) and `_select` (the previously-active
        button returns to idle) call this instead of re-inlining it.
        """
        button.configure(fg_color=_SIDEBAR_IDLE_FG_COLOR, hover_color=_SIDEBAR_IDLE_HOVER_COLOR)

    def _select(self, tool_id: str) -> None:
        """Swap the content area's single active view to `tool_id`'s.

        Detaches (never destroys) the current view via `pack_forget()` so
        its in-progress input state is preserved for when the user
        returns to it, then lazily instantiates (or reuses the cached)
        target view and mounts it (spec's "Sidebar/Registry Navigation"
        requirement, design §5). Also resets the previously-active
        sidebar button to its idle style and paints the newly-active one
        with the captured "active" style, so exactly one button shows
        which tool is currently selected.
        """
        if self._active_tool_id is not None:
            previous_button = self._sidebar_buttons.get(self._active_tool_id)
            if previous_button is not None:
                self._set_idle(previous_button)

        active_button = self._sidebar_buttons.get(tool_id)
        if active_button is not None:
            active_button.configure(
                fg_color=self._active_fg_color, hover_color=self._active_hover_color
            )
        self._active_tool_id = tool_id

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
