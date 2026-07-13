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

from app.core.exceptions import EntradaInvalidaError, PDFCorruptoError
from app.core.services.pdf_service import PDFService, SpanInfo
from app.ui.registry import PanelValues, SecretField
from app.ui.widgets.pdf_page_preview import PdfPagePreview
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
    "Replace text": "replace_text",
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
_EMPTY_REPLACEMENT_TEXT_MESSAGE = "Enter the replacement text before running."
_NO_SPAN_SELECTED_MESSAGE = "Click a word in the preview to select the text to replace."
_NO_SPAN_AT_CLICK_MESSAGE = "No text found here — click a word to select."

#: Display label the position dropdown shows once a preview click has
#: stored a point — never one of `_POSITION_PRESETS`, but `CTkOptionMenu`
#: accepts and displays it via `.set()` regardless (confirmed empirically,
#: `sdd/edit-pdf-preview/design` EMPIRICAL VERIFICATION RESULTS V4).
_CLICKED_POSITION_LABEL = "Custom (clicked)"

#: Preview box. Originally 260x280 (`sdd/edit-pdf-preview/design`'s V5
#: measurement) to fit the window's fixed height with zero scroll — since
#: `MainWindow.content` became a `CTkScrollableFrame`, height is no longer
#: a hard ceiling, so this was widened to a more comfortable reading size.
#: Letter-portrait at 500x650 renders to ~500x647px (zoom≈0.817).
_PREVIEW_MAX_W = 500
_PREVIEW_MAX_H = 650


class EditPanel(InputPanel):
    """Family F — mode-selector single-in/single-out panel (edit_pdf).

    Add text / Highlight text / Redact text / Replace text share one
    `SourceRow` + `SaveAsRow` shell behind an internal mode `CTkOptionMenu`
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
        self._click_point: tuple[float, float] | None = None
        self._selected_span: SpanInfo | None = None
        self._save_as_row = SaveAsRow(self, output_suffix, output_ext)
        self._source_row = SourceRow(self, on_change=self._on_source_change)

        self._mode_menu = ctk.CTkOptionMenu(
            self, values=list(_MODE_LABELS), command=self._on_mode_change
        )
        self._mode_menu.set(_DEFAULT_MODE_LABEL)

        # ADD group (rows 2-4) — only visible in add_text mode.
        self._add_page_label = ctk.CTkLabel(self, text="Page (1-based)")
        self._add_page_entry = ctk.CTkEntry(self)
        self._add_page_entry.bind("<FocusOut>", self._refresh_preview_evt)
        self._add_page_entry.bind("<Return>", self._refresh_preview_evt)
        self._insert_text_label = ctk.CTkLabel(self, text="Text to insert")
        self._insert_text_entry = ctk.CTkEntry(self)
        self._position_label = ctk.CTkLabel(self, text="Position")
        self._position_menu = ctk.CTkOptionMenu(
            self, values=list(_POSITION_PRESETS), command=self._on_position_select
        )
        self._position_menu.set(_DEFAULT_POSITION)
        self._add_group: tuple[tuple[ctk.CTkBaseClass, ctk.CTkBaseClass], ...] = (
            (self._add_page_label, self._add_page_entry),
            (self._insert_text_label, self._insert_text_entry),
            (self._position_label, self._position_menu),
        )
        self._preview = PdfPagePreview(self, on_point=self._on_preview_point)

        # SEARCH group (rows 2-3) — shared by highlight_text/redact_text.
        self._search_query_label = ctk.CTkLabel(self, text="Search text")
        self._search_query_entry = ctk.CTkEntry(self)
        self._search_page_label = ctk.CTkLabel(self, text="Page (blank = all pages)")
        self._search_page_entry = ctk.CTkEntry(self)
        self._search_group: tuple[tuple[ctk.CTkBaseClass, ctk.CTkBaseClass], ...] = (
            (self._search_query_label, self._search_query_entry),
            (self._search_page_label, self._search_page_entry),
        )

        # REPLACE group (rows 2-3) — click-to-select, own page entry
        # (distinct from `_add_page_entry`), no query entry, no position
        # dropdown (position is derived from the selected span).
        self._replace_page_label = ctk.CTkLabel(self, text="Page (1-based)")
        self._replace_page_entry = ctk.CTkEntry(self)
        self._replace_page_entry.bind("<FocusOut>", self._refresh_preview_evt)
        self._replace_page_entry.bind("<Return>", self._refresh_preview_evt)
        self._replacement_label = ctk.CTkLabel(self, text="Replacement text")
        self._replacement_entry = ctk.CTkEntry(self)
        self._replace_group: tuple[tuple[ctk.CTkBaseClass, ctk.CTkBaseClass], ...] = (
            (self._replace_page_label, self._replace_page_entry),
            (self._replacement_label, self._replacement_entry),
        )
        #: Panel-local inline hint for the replace-mode click outcome —
        #: distinct from `ToolView.status_label`, which is the Run-outcome
        #: surface (design D5).
        self._selection_feedback = ctk.CTkLabel(self, text="")

        self._source_row.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._mode_menu.grid(row=1, column=0, columnspan=2, sticky="w")
        self._grid_group(self._add_group)  # default mode = add_text
        self._preview.grid(row=5, column=0, columnspan=2, sticky="ew")
        self._save_as_row.grid(row=7, column=0, columnspan=2, sticky="ew")

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
        self._clear_click_point()
        self._clear_selection()
        self._forget_group(self._add_group)
        self._forget_group(self._search_group)
        self._forget_group(self._replace_group)
        self._selection_feedback.grid_forget()
        self._preview.grid_forget()
        self._mode = _MODE_BY_LABEL[value]
        if self._mode == "add_text":
            self._grid_group(self._add_group)
            self._preview.grid(row=5, column=0, columnspan=2, sticky="ew")
        elif self._mode == "replace_text":
            self._grid_group(self._replace_group)
            self._preview.grid(row=5, column=0, columnspan=2, sticky="ew")
            self._selection_feedback.grid(row=6, column=0, columnspan=2, sticky="w")
        else:
            self._grid_group(self._search_group)

    def _clear_click_point(self) -> None:
        """Reset any stored preview click and its dropdown display.

        A stored `_click_point` pertains to whichever page/document was
        rendered at the moment of the click — every event that can
        invalidate that render (a new source file, a different page
        number, or leaving `add_text` mode and coming back) MUST clear
        it too. Without this, `collect()` would keep forwarding a stale
        point captured against a page/document that's no longer the one
        selected, silently placing text at the wrong coordinates with no
        error (this was a real bug caught by review on this exact PR —
        see `sdd/edit-pdf-preview/apply-progress`). Distinct from
        `_on_position_select`, which ALSO clears `_click_point` but must
        NOT reset the dropdown display there, since it fires precisely
        because the user just picked a real preset the menu should keep
        showing.
        """
        self._click_point = None
        self._position_menu.set(_DEFAULT_POSITION)

    def _clear_selection(self) -> None:
        """Reset any stored replace-mode span selection and its overlay.

        Mirrors `_clear_click_point()` exactly, for the same reason: a
        stored `_selected_span` pertains to whichever page/document was
        rendered at the moment of the click, so every event that can
        invalidate that render (a new source file, a different page
        number, or leaving `replace_text` mode and coming back) MUST
        clear it too — the exact review-caught bug class documented on
        `_clear_click_point`, now guarded against for the replace-mode
        selection as well.
        """
        self._selected_span = None
        self._selection_feedback.configure(text="")
        self._preview.clear_marks()

    def _on_source_change(self, path: Path | None) -> None:
        """`SourceRow.on_change` fan-out target.

        `SourceRow` supports exactly one callback — this fans it out to
        both `SaveAsRow.set_source` (its original target) and a preview
        refresh (`sdd/edit-pdf-preview/design` D3), without changing
        `SourceRow`'s shared single-callback signature. Clears any
        stored click point/selection first — see `_clear_click_point`/
        `_clear_selection`.
        """
        self._clear_click_point()
        self._clear_selection()
        self._save_as_row.set_source(path)
        self._refresh_preview()

    def _on_preview_point(self, point: tuple[float, float]) -> None:
        """`PdfPagePreview.on_point` callback — a click stores the point.

        Branches on `self._mode`. In `add_text` mode, sets the position
        dropdown's DISPLAY to a non-preset label; the source of truth for
        `collect()` is `_click_point`, never this string
        (`sdd/edit-pdf-preview/design` D4) — and ALSO draws a marker at
        the click point. In `replace_text` mode, resolves the clicked
        point to a `SpanInfo` via `PDFService.find_span_at_point`: a hit
        stores the span, draws a selection box, and shows the selected
        text; a miss (or a local resolution error) clears the selection
        and shows a non-blocking inline message — never a crash, never
        silent (spec "Empty-Space Click Graceful Degradation").
        """
        if self._mode == "add_text":
            self._click_point = point
            self._position_menu.set(_CLICKED_POSITION_LABEL)
            self._preview.mark_point(point)
            return

        if self._mode != "replace_text":
            return

        try:
            source = self._source_row.path
            page = self._parse_page(self._replace_page_entry.get())
            if source is None:
                raise EntradaInvalidaError(_SELECT_SOURCE_AND_OUTPUT)
            span = PDFService().find_span_at_point(source, page, point)
        except (EntradaInvalidaError, PDFCorruptoError):
            self._selected_span = None
            self._preview.clear_marks()
            self._selection_feedback.configure(text=_NO_SPAN_AT_CLICK_MESSAGE)
            return

        if span is None:
            self._selected_span = None
            self._preview.clear_marks()
            self._selection_feedback.configure(text=_NO_SPAN_AT_CLICK_MESSAGE)
            return

        self._selected_span = span
        self._preview.mark_span(span.bbox)
        self._selection_feedback.configure(text=f"Selected: {span.text!r}")

    def _on_position_select(self, value: str) -> None:
        """`command=` target for `_position_menu` — clears any stored click point.

        Fires only on a real user pick (customtkinter routes `.set()`
        calls, including `_on_preview_point`'s, around `command=` — see
        `_on_mode_change`'s docstring for the same customtkinter
        behavior). `value` itself is unused: the menu's own `.get()`
        already reflects the picked preset by the time this runs. Does
        NOT call `_clear_click_point()` — that would stomp the preset
        the user just picked back to `_DEFAULT_POSITION`.
        """
        del value
        self._click_point = None

    def _active_page_entry(self) -> ctk.CTkEntry:
        """Return whichever group's page entry is live for `self._mode`.

        `add_text` and `replace_text` each own a distinct page entry
        (`_add_page_entry`/`_replace_page_entry`) — the preview render
        and any subsequent `find_span_at_point` call must always agree
        on which page is showing, so both `_refresh_preview` and
        `_on_preview_point` resolve the page through this single helper.
        """
        if self._mode == "replace_text":
            return self._replace_page_entry
        return self._add_page_entry

    def _refresh_preview(self) -> None:
        """Re-render the preview, or show a neutral placeholder.

        Graceful degradation only (`sdd/edit-pdf-preview/spec` "Preview
        Rendering Graceful Degradation") — this method never raises and
        never blocks `Run`; full validation stays `collect()`'s and the
        service's job. Does NOT clear `_click_point`/`_selected_span`
        itself — callers that can genuinely invalidate a stored point/
        selection (`_on_source_change`, `_refresh_preview_evt`) call
        `_clear_click_point()`/`_clear_selection()` themselves before
        refreshing, so this method stays a pure render/placeholder step
        reusable without that side effect.
        """
        source = self._source_row.path
        if source is None:
            self._preview.show_placeholder()
            return
        try:
            page = self._parse_page(self._active_page_entry().get())
            result = PDFService().render_page(source, page, _PREVIEW_MAX_W, _PREVIEW_MAX_H)
        except (EntradaInvalidaError, PDFCorruptoError):
            self._preview.show_placeholder()
            return
        self._preview.show(result)

    def _refresh_preview_evt(self, event: object) -> None:
        """`<FocusOut>`/`<Return>` bind wrapper for the page-number entry.

        A page-number change can point the preview at a different page
        of the same document, invalidating any stored click/selection —
        clears both (see `_clear_click_point`/`_clear_selection`) before
        re-rendering.
        """
        del event
        self._clear_click_point()
        self._clear_selection()
        self._refresh_preview()

    def collect(self) -> PanelValues:
        source = self._source_row.path
        output = self._save_as_row.output
        self._require_source_and_output(source, output)

        if self._mode == "add_text":
            page = self._parse_page(self._add_page_entry.get())
            insert_text = self._insert_text_entry.get()
            if not insert_text.strip():
                raise EntradaInvalidaError(_EMPTY_INSERT_TEXT_MESSAGE)
            if self._click_point is not None:
                return PanelValues(
                    mode="add_text",
                    source=source,
                    output=output,
                    page=page,
                    insert_text=insert_text,
                    position=_DEFAULT_POSITION,
                    point=self._click_point,
                )
            position = self._position_menu.get()
            return PanelValues(
                mode="add_text",
                source=source,
                output=output,
                page=page,
                insert_text=insert_text,
                position=position,
            )

        if self._mode == "replace_text":
            page = self._parse_page(self._replace_page_entry.get())
            replacement = self._replacement_entry.get()
            if not replacement.strip():
                raise EntradaInvalidaError(_EMPTY_REPLACEMENT_TEXT_MESSAGE)
            if self._selected_span is None:
                raise EntradaInvalidaError(_NO_SPAN_SELECTED_MESSAGE)
            return PanelValues(
                mode="replace_text",
                source=source,
                output=output,
                page=page,
                selected_span=self._selected_span,
                replacement=replacement,
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
