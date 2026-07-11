"""Reusable row widgets shared by all 5 family input panels.

Design: `sdd/acrobat-tools-ui/design` §4. Each row owns its own
`tkinter.filedialog` call and stores the resulting selection on itself —
rows never validate or raise; presence/format guards are each family
panel's job (`app.ui.widgets.panels`, ADR-004). `customtkinter` does not
wrap `filedialog` (design §9 item 5), so these rows call the stdlib
`tkinter.filedialog` module directly.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app.ui.registry import suggest_output_name

_NO_FILE_SELECTED = "No file selected"
_NO_FOLDER_SELECTED = "No folder selected"
_NO_FILES_SELECTED = "No files selected"


class SourceRow(ctk.CTkFrame):
    """One source-file picker: label + path display + Browse button.

    Stores the chosen `Path` (or `None` before any selection) and fires an
    optional `on_change(path)` hook after every successful pick — family
    panels wire this to refresh a sibling `SaveAsRow`'s filename
    suggestion (design §4/§8).
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_change: Callable[[Path | None], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._on_change = on_change
        self._path: Path | None = None

        ctk.CTkLabel(self, text="Source file").grid(row=0, column=0, sticky="w")
        self._path_label = ctk.CTkLabel(self, text=_NO_FILE_SELECTED)
        self._path_label.grid(row=0, column=1, sticky="w", padx=(8, 8))
        ctk.CTkButton(self, text="Browse…", command=self._browse).grid(row=0, column=2)

    @property
    def path(self) -> Path | None:
        """The currently selected source file, or `None` if unset."""
        return self._path

    def _browse(self) -> None:
        selected = filedialog.askopenfilename()
        if not selected:
            return
        self._path = Path(selected)
        self._path_label.configure(text=self._path.name)
        if self._on_change is not None:
            self._on_change(self._path)


class SaveAsRow(ctk.CTkFrame):
    """One destination-file picker: label + path display + Choose button.

    Pre-fills the save dialog with a suggested filename derived from the
    most recently known source (`set_source`), per design §8's
    `suggest_output_name()` algorithm. Family A/D/E panels call
    `set_source` from their `SourceRow`'s `on_change` hook; family B calls
    it with `inputs[0]` whenever its file list changes.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        output_suffix: str = "",
        output_ext: str = "",
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._output_suffix = output_suffix
        self._output_ext = output_ext
        self._source: Path | None = None
        self._output: Path | None = None

        ctk.CTkLabel(self, text="Save as").grid(row=0, column=0, sticky="w")
        self._path_label = ctk.CTkLabel(self, text=_NO_FILE_SELECTED)
        self._path_label.grid(row=0, column=1, sticky="w", padx=(8, 8))
        ctk.CTkButton(self, text="Choose…", command=self._browse).grid(row=0, column=2)

    @property
    def output(self) -> Path | None:
        """The currently selected destination file, or `None` if unset."""
        return self._output

    def set_source(self, source: Path | None) -> None:
        """Update the source used to derive the next save-dialog suggestion.

        Called by the owning panel whenever its source selection changes —
        design §8: "Suggestion recomputed whenever the source changes."
        """
        self._source = source

    def _browse(self) -> None:
        initialdir = str(self._source.parent) if self._source is not None else ""
        initialfile = (
            suggest_output_name(self._source, self._output_suffix, self._output_ext)
            if self._source is not None
            else ""
        )
        selected = filedialog.asksaveasfilename(
            initialdir=initialdir,
            initialfile=initialfile,
            defaultextension=self._output_ext,
        )
        if not selected:
            return
        self._output = Path(selected)
        self._path_label.configure(text=self._output.name)


class DirectoryRow(ctk.CTkFrame):
    """One output-directory picker: label + path display + Choose button.

    Family C (`split`) uses this instead of `SaveAsRow` — split's output
    is a directory of page files, not a single destination file (design
    §4).
    """

    def __init__(self, master: ctk.CTkBaseClass) -> None:
        super().__init__(master, fg_color="transparent")
        self._path: Path | None = None

        ctk.CTkLabel(self, text="Output folder").grid(row=0, column=0, sticky="w")
        self._path_label = ctk.CTkLabel(self, text=_NO_FOLDER_SELECTED)
        self._path_label.grid(row=0, column=1, sticky="w", padx=(8, 8))
        ctk.CTkButton(self, text="Choose…", command=self._browse).grid(row=0, column=2)

    @property
    def path(self) -> Path | None:
        """The currently selected output directory, or `None` if unset."""
        return self._path

    def _browse(self) -> None:
        selected = filedialog.askdirectory()
        if not selected:
            return
        self._path = Path(selected)
        self._path_label.configure(text=self._path.name or str(self._path))


class PasswordRow(ctk.CTkFrame):
    """One masked password field: label + `CTkEntry(show="*")`.

    Wraps a single `SecretField` (design §5) — masking is empirically
    confirmed safe (design §9 item 4: `.get()` still returns cleartext
    while the widget displays `*`). `required` is exposed for the owning
    `SecretPanel.collect()` guard (design §4, ADR-004); this row itself
    never validates or raises.
    """

    def __init__(self, master: ctk.CTkBaseClass, label: str, required: bool) -> None:
        super().__init__(master, fg_color="transparent")
        self.required = required

        ctk.CTkLabel(self, text=label).grid(row=0, column=0, sticky="w")
        self._entry = ctk.CTkEntry(self, show="*")
        self._entry.grid(row=0, column=1, sticky="w", padx=(8, 0))

    @property
    def value(self) -> str:
        """The entered password text (may be empty)."""
        return self._entry.get()


class FileListEditor(ctk.CTkFrame):
    """Ordered, reorderable multi-file picker for family B panels.

    `CTkButton("Add files…")` opens a multi-select `askopenfilenames`
    dialog and appends the results (in dialog order) to an internal
    ordered list. A `CTkScrollableFrame` renders one row per file — name
    label + Up/Down/Remove buttons — and is fully destroyed/rebuilt on
    every mutation (add/remove/reorder); design §9 item 2 empirically
    confirmed this destroy/rebuild pattern is safe under 50x churn.

    Fires an optional `on_change(files)` hook after every mutation so the
    owning panel can refresh a sibling `SaveAsRow`'s suggestion from
    `files[0]` (design §4).
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_change: Callable[[list[Path]], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._on_change = on_change
        self._files: list[Path] = []

        ctk.CTkButton(self, text="Add files…", command=self._add_files).grid(
            row=0, column=0, sticky="w"
        )
        self._list_frame = ctk.CTkScrollableFrame(self, height=120)
        self._list_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        ctk.CTkLabel(self._list_frame, text=_NO_FILES_SELECTED).grid(
            row=0, column=0, sticky="w"
        )

    @property
    def files(self) -> list[Path]:
        """The current ordered file list (a copy — mutating it does not affect state)."""
        return list(self._files)

    def _add_files(self) -> None:
        selected = filedialog.askopenfilenames()
        if not selected:
            return
        self._files.extend(Path(p) for p in selected)
        self._rebuild()

    def _move_up(self, index: int) -> None:
        if index <= 0:
            return
        self._files[index - 1], self._files[index] = self._files[index], self._files[index - 1]
        self._rebuild()

    def _move_down(self, index: int) -> None:
        if index >= len(self._files) - 1:
            return
        self._files[index + 1], self._files[index] = self._files[index], self._files[index + 1]
        self._rebuild()

    def _remove(self, index: int) -> None:
        del self._files[index]
        self._rebuild()

    def _rebuild(self) -> None:
        for child in self._list_frame.winfo_children():
            child.destroy()

        if not self._files:
            ctk.CTkLabel(self._list_frame, text=_NO_FILES_SELECTED).grid(
                row=0, column=0, sticky="w"
            )
        else:
            for row_index, file_path in enumerate(self._files):
                ctk.CTkLabel(self._list_frame, text=file_path.name).grid(
                    row=row_index, column=0, sticky="w"
                )
                ctk.CTkButton(
                    self._list_frame,
                    text="↑",
                    width=28,
                    command=partial(self._move_up, row_index),
                ).grid(row=row_index, column=1)
                ctk.CTkButton(
                    self._list_frame,
                    text="↓",
                    width=28,
                    command=partial(self._move_down, row_index),
                ).grid(row=row_index, column=2)
                ctk.CTkButton(
                    self._list_frame,
                    text="Remove",
                    width=64,
                    command=partial(self._remove, row_index),
                ).grid(row=row_index, column=3)

        if self._on_change is not None:
            self._on_change(list(self._files))
