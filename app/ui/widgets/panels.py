"""6 family input panels — `sdd/acrobat-tools-ui/design` §4 (Family A-E) and
`sdd/edit-pdf/design` (Family F, `EditPanel`).

Each panel subclasses `InputPanel(ctk.CTkFrame)` and exposes
`collect() -> PanelValues`, performing its family's LOCAL GUARD (ADR-004,
generalized across all 6 families per the finished spec): raising
`EntradaInvalidaError` synchronously when required input is missing or
malformed, before `ToolView._on_run` (PR3) ever calls
`TaskRunner.submit`. This keeps the pre-submit local-validation path and
the post-submit service-raised-exception path funneling through the
identical `error_message()` resolver (`app.ui.errors`) — there is exactly
one place an exception becomes user-facing text (design §6).

Reusable rows live in `app.ui.widgets.rows` and own their own
`filedialog` calls; panels never touch `tkinter.filedialog` directly.
"""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from app.core.exceptions import EntradaInvalidaError
from app.ui.registry import PanelValues, SecretField
from app.ui.widgets.rows import (
    DirectoryRow,
    FileListEditor,
    PasswordRow,
    SaveAsRow,
    SourceRow,
)

_SELECT_SOURCE_AND_OUTPUT = "Select both a source file and a destination file before running."


class InputPanel(ctk.CTkFrame):
    """Common base for all 6 family panels.

    Concrete subclasses mount their family's rows in `__init__` and
    override `collect()`. The base itself defines no rows — the input
    shape is entirely a subclass concern (design §4).
    """

    def collect(self) -> PanelValues:
        """Read the panel's current widget state into a `PanelValues`.

        Must raise `EntradaInvalidaError` — never return a partially
        filled value the caller has to re-check — whenever the family's
        required fields are missing or malformed. Subclasses MUST
        override this.
        """
        raise NotImplementedError

    @staticmethod
    def _require_source_and_output(source: Path | None, output: Path | None) -> None:
        """Shared guard for every family whose panel needs both (A/D/E).

        Single source of truth for the "select both a source file and a
        destination file" rule (ADR-004) — subclasses must not re-inline
        this check, so a future change to it only needs one edit site.
        """
        if source is None or output is None:
            raise EntradaInvalidaError(_SELECT_SOURCE_AND_OUTPUT)


class SingleInSingleOutPanel(InputPanel):
    """Family A — single source file, single destination file.

    compress, ocr, convertir, pdf_a_word, pdf_a_excel (design §4). Guard:
    raises `EntradaInvalidaError` if `source` or `output` is unset (spec
    "Family A Panel — Single-in/Single-out": "block Run until both are
    selected").
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        output_suffix: str = "",
        output_ext: str = "",
    ) -> None:
        super().__init__(master)
        self._save_as_row = SaveAsRow(self, output_suffix, output_ext)
        self._source_row = SourceRow(self, on_change=self._save_as_row.set_source)

        self._source_row.grid(row=0, column=0, sticky="ew")
        self._save_as_row.grid(row=1, column=0, sticky="ew")

    def collect(self) -> PanelValues:
        source = self._source_row.path
        output = self._save_as_row.output
        self._require_source_and_output(source, output)
        return PanelValues(source=source, output=output)


class MultiInSingleOutPanel(InputPanel):
    """Family B — ordered, non-empty list of source files, single destination.

    merge, jpg_to_pdf, scan_to_pdf (design §4). Guard: raises
    `EntradaInvalidaError` if the file list is empty or `output` is unset
    (spec "Family B Panel — Multi-in/Single-out": "Empty file list blocks
    Run").
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        output_suffix: str = "",
        output_ext: str = "",
    ) -> None:
        super().__init__(master)
        self._save_as_row = SaveAsRow(self, output_suffix, output_ext)
        self._file_list_editor = FileListEditor(self, on_change=self._on_files_changed)

        self._file_list_editor.grid(row=0, column=0, sticky="ew")
        self._save_as_row.grid(row=1, column=0, sticky="ew")

    def _on_files_changed(self, files: list[Path]) -> None:
        self._save_as_row.set_source(files[0] if files else None)

    def collect(self) -> PanelValues:
        inputs = self._file_list_editor.files
        output = self._save_as_row.output
        if not inputs or output is None:
            raise EntradaInvalidaError(
                "Add at least one source file and choose a destination file before running."
            )
        return PanelValues(inputs=inputs, output=output)


class SingleInDirOutPanel(InputPanel):
    """Family C — single source file, output directory (split only).

    No `SaveAsRow`, no ranges field — split always writes page-per-file
    into a chosen directory and its `run` callable binds `ranges=None`
    literally (design §4/ADR-008). Guard: raises `EntradaInvalidaError`
    if `source` or `output_dir` is unset.
    """

    def __init__(self, master: ctk.CTkBaseClass) -> None:
        super().__init__(master)
        self._source_row = SourceRow(self)
        self._directory_row = DirectoryRow(self)

        self._source_row.grid(row=0, column=0, sticky="ew")
        self._directory_row.grid(row=1, column=0, sticky="ew")

    def collect(self) -> PanelValues:
        source = self._source_row.path
        output_dir = self._directory_row.path
        if source is None or output_dir is None:
            raise EntradaInvalidaError(
                "Select both a source file and an output folder before running."
            )
        return PanelValues(source=source, output_dir=output_dir)


class SecretPanel(InputPanel):
    """Family D — one or more masked secret fields + single-in/single-out.

    protect (owner_password required, user_password optional), unlock
    (password required) (design §4). Guard: raises `EntradaInvalidaError`
    if `source`/`output` is unset OR any REQUIRED `SecretField` is empty
    (spec "Family D Panel": "Empty required password blocks Run without
    calling the service"). Optional fields may stay empty — collected as
    `""`; the owning `ToolSpec.run` lambda maps that to `None`.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        secret_fields: tuple[SecretField, ...],
        output_suffix: str = "",
        output_ext: str = "",
    ) -> None:
        super().__init__(master)
        self._secret_fields = secret_fields
        self._save_as_row = SaveAsRow(self, output_suffix, output_ext)
        self._source_row = SourceRow(self, on_change=self._save_as_row.set_source)
        self._password_rows: dict[str, PasswordRow] = {
            field.key: PasswordRow(self, field.label) for field in secret_fields
        }

        self._source_row.grid(row=0, column=0, sticky="ew")
        for row_index, field in enumerate(secret_fields, start=1):
            self._password_rows[field.key].grid(row=row_index, column=0, sticky="ew")
        self._save_as_row.grid(row=len(secret_fields) + 1, column=0, sticky="ew")

    def collect(self) -> PanelValues:
        source = self._source_row.path
        output = self._save_as_row.output
        self._require_source_and_output(source, output)

        secrets: dict[str, str] = {}
        for field in self._secret_fields:
            value = self._password_rows[field.key].value
            if field.required and not value:
                raise EntradaInvalidaError(f"{field.label} is required before running.")
            secrets[field.key] = value

        return PanelValues(source=source, output=output, secrets=secrets)


class OrderPanel(InputPanel):
    """Family E — comma-separated page-order string + single-in/single-out.

    organize only (design §4). Guard: raises `EntradaInvalidaError` if
    `source`/`output` is unset OR the order string fails to parse into
    `list[int]` (empty field, non-integer tokens, malformed separators —
    spec "Family E Panel": "Invalid order blocks submission locally").
    Uniqueness/1-based-validity remains the service's job — only
    presence/parseability is checked here (design §4).
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        output_suffix: str = "",
        output_ext: str = "",
    ) -> None:
        super().__init__(master)
        self._save_as_row = SaveAsRow(self, output_suffix, output_ext)
        self._source_row = SourceRow(self, on_change=self._save_as_row.set_source)
        ctk.CTkLabel(self, text="Page order (comma-separated, e.g. 3,1,2,4)").grid(
            row=1, column=0, sticky="w"
        )
        self._order_entry = ctk.CTkEntry(self)

        self._source_row.grid(row=0, column=0, sticky="ew")
        self._order_entry.grid(row=2, column=0, sticky="ew")
        self._save_as_row.grid(row=3, column=0, sticky="ew")

    def collect(self) -> PanelValues:
        source = self._source_row.path
        output = self._save_as_row.output
        self._require_source_and_output(source, output)

        order = self._parse_order(self._order_entry.get())
        return PanelValues(source=source, output=output, order=order)

    @staticmethod
    def _parse_order(raw: str) -> list[int]:
        tokens = [token.strip() for token in raw.split(",")]
        if not raw.strip() or any(not token for token in tokens):
            raise EntradaInvalidaError(
                "Enter a comma-separated list of page numbers, e.g. 3,1,2,4."
            )
        try:
            return [int(token) for token in tokens]
        except ValueError as exc:
            raise EntradaInvalidaError(
                "Enter a comma-separated list of page numbers, e.g. 3,1,2,4."
            ) from exc


#: `CTkOptionMenu` label -> `PanelValues.mode` string, per `sdd/edit-pdf/spec`
#: "EditPanel Mode-Selector Field Visibility".
_MODE_BY_LABEL: dict[str, str] = {
    "Add text": "add_text",
    "Highlight text": "highlight_text",
    "Redact text": "redact_text",
}
_MODE_LABELS: tuple[str, ...] = tuple(_MODE_BY_LABEL)
_DEFAULT_MODE_LABEL = "Add text"

_POSITION_PRESETS: tuple[str, ...] = (
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "center",
)
_DEFAULT_POSITION = "top-left"

_EMPTY_INSERT_TEXT_MESSAGE = "Enter the text to insert before running."
_EMPTY_SEARCH_QUERY_MESSAGE = "Enter the text to search for before running."
_INVALID_PAGE_MESSAGE = "Enter a valid page number (1 or greater)."


class EditPanel(InputPanel):
    """Family F — mode-selector single-in/single-out panel (edit_pdf).

    Add text / Highlight text / Redact text share one `SourceRow` +
    `SaveAsRow` shell behind an internal mode `CTkOptionMenu`
    (`sdd/edit-pdf/design` "UI Design" §`EditPanel`). `_on_mode_change`
    `grid_forget()`s the inactive mode's field group and `grid()`s the
    active one at its fixed rows — empty grid rows collapse to zero
    height, so no visual gap. Default mode on construction is
    `add_text` (spec "EditPanel Mode-Selector Field Visibility").

    **Testing note (design's empirical finding, confirmed this
    session)**: `CTkOptionMenu(command=cb)` fires `cb` on a REAL user
    click (routed through the widget's internal `_dropdown_callback`),
    but NOT on `.set()`. Any test that needs to exercise a mode switch
    MUST call `panel._on_mode_change(value)` directly — calling
    `panel._mode_menu.set(value)` will NOT invoke `_on_mode_change` and
    the field groups will silently fail to swap.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        output_suffix: str = "",
        output_ext: str = "",
    ) -> None:
        super().__init__(master)
        self._mode: str = _MODE_BY_LABEL[_DEFAULT_MODE_LABEL]
        self._save_as_row = SaveAsRow(self, output_suffix, output_ext)
        self._source_row = SourceRow(self, on_change=self._save_as_row.set_source)

        self._mode_menu = ctk.CTkOptionMenu(
            self, values=list(_MODE_LABELS), command=self._on_mode_change
        )
        self._mode_menu.set(_DEFAULT_MODE_LABEL)

        # ADD group (rows 2-4) — only visible in add_text mode.
        self._add_page_label = ctk.CTkLabel(self, text="Page (1-based)")
        self._add_page_entry = ctk.CTkEntry(self)
        self._insert_text_label = ctk.CTkLabel(self, text="Text to insert")
        self._insert_text_entry = ctk.CTkEntry(self)
        self._position_label = ctk.CTkLabel(self, text="Position")
        self._position_menu = ctk.CTkOptionMenu(self, values=list(_POSITION_PRESETS))
        self._position_menu.set(_DEFAULT_POSITION)
        self._add_group: tuple[tuple[ctk.CTkBaseClass, ctk.CTkBaseClass], ...] = (
            (self._add_page_label, self._add_page_entry),
            (self._insert_text_label, self._insert_text_entry),
            (self._position_label, self._position_menu),
        )

        # SEARCH group (rows 2-3) — shared by highlight_text/redact_text.
        self._search_query_label = ctk.CTkLabel(self, text="Search text")
        self._search_query_entry = ctk.CTkEntry(self)
        self._search_page_label = ctk.CTkLabel(self, text="Page (blank = all pages)")
        self._search_page_entry = ctk.CTkEntry(self)
        self._search_group: tuple[tuple[ctk.CTkBaseClass, ctk.CTkBaseClass], ...] = (
            (self._search_query_label, self._search_query_entry),
            (self._search_page_label, self._search_page_entry),
        )

        self._source_row.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._mode_menu.grid(row=1, column=0, columnspan=2, sticky="w")
        self._grid_group(self._add_group)  # default mode = add_text
        self._save_as_row.grid(row=5, column=0, columnspan=2, sticky="ew")

    @staticmethod
    def _grid_group(group: tuple[tuple[ctk.CTkBaseClass, ctk.CTkBaseClass], ...]) -> None:
        for row_index, (label, widget) in enumerate(group, start=2):
            label.grid(row=row_index, column=0, sticky="w")
            widget.grid(row=row_index, column=1, sticky="ew")

    @staticmethod
    def _forget_group(group: tuple[tuple[ctk.CTkBaseClass, ctk.CTkBaseClass], ...]) -> None:
        for label, widget in group:
            label.grid_forget()
            widget.grid_forget()

    def _on_mode_change(self, value: str) -> None:
        """`command=` target for `_mode_menu` — swaps the visible field group.

        Real user clicks route through customtkinter's internal
        `_dropdown_callback`, which invokes this. `.set()` does NOT (see
        class docstring) — tests must call this method directly.
        """
        self._forget_group(self._add_group)
        self._forget_group(self._search_group)
        self._mode = _MODE_BY_LABEL[value]
        if self._mode == "add_text":
            self._grid_group(self._add_group)
        else:
            self._grid_group(self._search_group)

    def collect(self) -> PanelValues:
        source = self._source_row.path
        output = self._save_as_row.output
        self._require_source_and_output(source, output)

        if self._mode == "add_text":
            page = self._parse_page(self._add_page_entry.get())
            insert_text = self._insert_text_entry.get()
            if not insert_text.strip():
                raise EntradaInvalidaError(_EMPTY_INSERT_TEXT_MESSAGE)
            position = self._position_menu.get()
            return PanelValues(
                mode="add_text",
                source=source,
                output=output,
                page=page,
                insert_text=insert_text,
                position=position,
            )

        search_query = self._search_query_entry.get()
        if not search_query.strip():
            raise EntradaInvalidaError(_EMPTY_SEARCH_QUERY_MESSAGE)
        page = self._parse_optional_page(self._search_page_entry.get())
        return PanelValues(
            mode=self._mode,
            source=source,
            output=output,
            search_query=search_query,
            page=page,
        )

    @staticmethod
    def _parse_page(raw: str) -> int:
        """Parse a required 1-based page entry (add_text mode)."""
        try:
            page = int(raw.strip())
        except ValueError as exc:
            raise EntradaInvalidaError(_INVALID_PAGE_MESSAGE) from exc
        if page < 1:
            raise EntradaInvalidaError(_INVALID_PAGE_MESSAGE)
        return page

    @staticmethod
    def _parse_optional_page(raw: str) -> int | None:
        """Parse an optional page entry (highlight/redact mode) — blank = all pages."""
        if not raw.strip():
            return None
        return EditPanel._parse_page(raw)
