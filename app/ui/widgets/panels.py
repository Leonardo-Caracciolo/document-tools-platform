"""5 family input panels — `sdd/acrobat-tools-ui/design` §4.

Each panel subclasses `InputPanel(ctk.CTkFrame)` and exposes
`collect() -> PanelValues`, performing its family's LOCAL GUARD (ADR-004,
generalized across all 5 families per the finished spec): raising
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
    """Common base for all 5 family panels.

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
        if source is None or output is None:
            raise EntradaInvalidaError(_SELECT_SOURCE_AND_OUTPUT)
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
            field.key: PasswordRow(self, field.label, field.required) for field in secret_fields
        }

        self._source_row.grid(row=0, column=0, sticky="ew")
        for row_index, field in enumerate(secret_fields, start=1):
            self._password_rows[field.key].grid(row=row_index, column=0, sticky="ew")
        self._save_as_row.grid(row=len(secret_fields) + 1, column=0, sticky="ew")

    def collect(self) -> PanelValues:
        source = self._source_row.path
        output = self._save_as_row.output
        if source is None or output is None:
            raise EntradaInvalidaError(_SELECT_SOURCE_AND_OUTPUT)

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
        if source is None or output is None:
            raise EntradaInvalidaError(_SELECT_SOURCE_AND_OUTPUT)

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
