"""Tests for the 6 family input panels' `collect()` guard paths — PR2 scope
(Family A-E), extended in `sdd/edit-pdf` PR2 for `EditPanel` (Family F).

First live-Tk-root test module in this project (tasks artifact's Testing
Strategy #3): `InputPanel` subclasses extend `ctk.CTkFrame`, so `collect()`
can only be exercised against a real Tk root. Scoped narrowly to
guard-path assertions per spec scenarios — NOT full widget
rendering/visual tests. `filedialog` is never invoked: each row's stored
value is set directly on its private attribute, bypassing the dialog
entirely.

Uses the session-scoped `tk_root` fixture from `tests/conftest.py`
(shared with `test_ui_tool_view_lifecycle.py`) rather than defining its
own — Tkinter does not reliably support multiple `Tk()` roots per process.

**`EditPanel` mode-switch testing caveat (`sdd/edit-pdf/design` EMPIRICAL
VERIFICATION RESULTS item 6, confirmed empirically this session against
the real installed customtkinter 6.0.0)**: `CTkOptionMenu(command=cb)`
fires `cb` on a REAL user click (routed through the widget's internal
`_dropdown_callback`), but calling `.set(value)` does NOT invoke
`command=` at all. `TestEditPanel` below therefore drives mode switches by
calling `panel._on_mode_change(value)` directly — NEVER
`panel._mode_menu.set(value)` — because the latter would silently no-op
(the field groups would never swap) and a test relying on it would fail
in a confusing way. If you see a test using `.set()` to switch mode fail
silently, this is why — do not chase it as a production bug.
"""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk
import pytest

from app.core.exceptions import EntradaInvalidaError
from app.ui.registry import SecretField
from app.ui.widgets.panels import (
    EditPanel,
    MultiInSingleOutPanel,
    OrderPanel,
    SecretPanel,
    SingleInDirOutPanel,
    SingleInSingleOutPanel,
)


class TestSingleInSingleOutPanel:
    def test_missing_source_and_output_raises(self, tk_root: ctk.CTk) -> None:
        panel = SingleInSingleOutPanel(tk_root, output_suffix="_x", output_ext=".pdf")

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_missing_output_only_raises(self, tk_root: ctk.CTk) -> None:
        panel = SingleInSingleOutPanel(tk_root, output_suffix="_x", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_valid_source_and_output_returns_panel_values(self, tk_root: ctk.CTk) -> None:
        panel = SingleInSingleOutPanel(tk_root, output_suffix="_x", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")

        values = panel.collect()

        assert values.source == Path("in.pdf")
        assert values.output == Path("out.pdf")


class TestMultiInSingleOutPanel:
    def test_empty_file_list_raises(self, tk_root: ctk.CTk) -> None:
        panel = MultiInSingleOutPanel(tk_root, output_suffix="_x", output_ext=".pdf")
        panel._save_as_row._output = Path("out.pdf")

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_missing_output_raises(self, tk_root: ctk.CTk) -> None:
        panel = MultiInSingleOutPanel(tk_root, output_suffix="_x", output_ext=".pdf")
        panel._file_list_editor._files = [Path("a.pdf"), Path("b.pdf")]

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_valid_inputs_and_output_returns_panel_values(self, tk_root: ctk.CTk) -> None:
        panel = MultiInSingleOutPanel(tk_root, output_suffix="_x", output_ext=".pdf")
        panel._file_list_editor._files = [Path("a.pdf"), Path("b.pdf")]
        panel._save_as_row._output = Path("out.pdf")

        values = panel.collect()

        assert values.inputs == [Path("a.pdf"), Path("b.pdf")]
        assert values.output == Path("out.pdf")


class TestSingleInDirOutPanel:
    def test_missing_source_or_dir_raises(self, tk_root: ctk.CTk) -> None:
        panel = SingleInDirOutPanel(tk_root)

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_valid_source_and_dir_returns_panel_values(self, tk_root: ctk.CTk) -> None:
        panel = SingleInDirOutPanel(tk_root)
        panel._source_row._path = Path("in.pdf")
        panel._directory_row._path = Path("out_dir")

        values = panel.collect()

        assert values.source == Path("in.pdf")
        assert values.output_dir == Path("out_dir")


class TestSecretPanel:
    _PROTECT_FIELDS = (
        SecretField("owner_password", "Owner password", True),
        SecretField("user_password", "User password (optional)", False),
    )
    _UNLOCK_FIELDS = (SecretField("password", "Password", True),)

    def test_empty_required_password_raises(self, tk_root: ctk.CTk) -> None:
        panel = SecretPanel(tk_root, self._UNLOCK_FIELDS, output_suffix="_x", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        # password entry left empty

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_missing_source_or_output_raises_even_with_password_filled(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = SecretPanel(tk_root, self._UNLOCK_FIELDS, output_suffix="_x", output_ext=".pdf")
        panel._password_rows["password"]._entry.insert(0, "secret")

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_optional_empty_password_does_not_raise(self, tk_root: ctk.CTk) -> None:
        panel = SecretPanel(tk_root, self._PROTECT_FIELDS, output_suffix="_x", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._password_rows["owner_password"]._entry.insert(0, "owner-secret")
        # user_password intentionally left empty (optional)

        values = panel.collect()

        assert values.secrets == {"owner_password": "owner-secret", "user_password": ""}

    def test_valid_required_and_optional_returns_panel_values(self, tk_root: ctk.CTk) -> None:
        panel = SecretPanel(tk_root, self._PROTECT_FIELDS, output_suffix="_x", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._password_rows["owner_password"]._entry.insert(0, "owner-secret")
        panel._password_rows["user_password"]._entry.insert(0, "user-secret")

        values = panel.collect()

        assert values.source == Path("in.pdf")
        assert values.output == Path("out.pdf")
        assert values.secrets == {
            "owner_password": "owner-secret",
            "user_password": "user-secret",
        }


class TestOrderPanel:
    def test_missing_source_or_output_raises(self, tk_root: ctk.CTk) -> None:
        panel = OrderPanel(tk_root, output_suffix="_organized", output_ext=".pdf")
        panel._order_entry.insert(0, "3,1,2,4")

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_empty_order_string_raises(self, tk_root: ctk.CTk) -> None:
        panel = OrderPanel(tk_root, output_suffix="_organized", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_unparseable_order_string_raises(self, tk_root: ctk.CTk) -> None:
        panel = OrderPanel(tk_root, output_suffix="_organized", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._order_entry.insert(0, "3,a,2")

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_valid_order_string_parses_and_returns_panel_values(self, tk_root: ctk.CTk) -> None:
        panel = OrderPanel(tk_root, output_suffix="_organized", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._order_entry.insert(0, "3,1,2,4")

        values = panel.collect()

        assert values.order == [3, 1, 2, 4]


class TestEditPanel:
    """`sdd/edit-pdf/spec` "EditPanel Mode-Selector Field Visibility"."""

    def test_default_mode_is_add_text_with_add_group_visible(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")

        assert panel._mode == "add_text"
        assert panel._add_page_entry.grid_info() != {}
        assert panel._insert_text_entry.grid_info() != {}
        assert panel._position_menu.grid_info() != {}
        assert panel._search_query_entry.grid_info() == {}
        assert panel._search_page_entry.grid_info() == {}

    def test_on_mode_change_swaps_visible_field_groups(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")

        # Per the module docstring's empirical finding: driving
        # `panel._mode_menu.set(...)` would NOT fire this callback on a
        # real customtkinter widget, so the test calls it directly.
        panel._on_mode_change("Highlight text")

        assert panel._mode == "highlight_text"
        assert panel._add_page_entry.grid_info() == {}
        assert panel._insert_text_entry.grid_info() == {}
        assert panel._position_menu.grid_info() == {}
        assert panel._search_query_entry.grid_info() != {}
        assert panel._search_page_entry.grid_info() != {}

    def test_on_mode_change_to_redact_text_also_shows_search_group(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")

        panel._on_mode_change("Redact text")

        assert panel._mode == "redact_text"
        assert panel._search_query_entry.grid_info() != {}
        assert panel._search_page_entry.grid_info() != {}

    def test_on_mode_change_back_to_add_text_restores_add_group(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Highlight text")

        panel._on_mode_change("Add text")

        assert panel._mode == "add_text"
        assert panel._add_page_entry.grid_info() != {}
        assert panel._search_query_entry.grid_info() == {}

    def test_add_text_mode_missing_source_and_output_raises(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._add_page_entry.insert(0, "2")
        panel._insert_text_entry.insert(0, "hello")

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_add_text_mode_missing_page_raises(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._insert_text_entry.insert(0, "hello")
        # page entry intentionally left blank

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_add_text_mode_empty_insert_text_raises(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._add_page_entry.insert(0, "2")
        # insert-text entry intentionally left blank

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_add_text_mode_valid_inputs_returns_panel_values(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._add_page_entry.insert(0, "2")
        panel._insert_text_entry.insert(0, "hello")
        panel._position_menu.set("bottom-right")

        values = panel.collect()

        assert values.mode == "add_text"
        assert values.source == Path("in.pdf")
        assert values.output == Path("out.pdf")
        assert values.page == 2
        assert values.insert_text == "hello"
        assert values.position == "bottom-right"

    def test_highlight_text_mode_missing_source_and_output_raises(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Highlight text")
        panel._search_query_entry.insert(0, "invoice")

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_highlight_text_mode_empty_search_query_raises(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Highlight text")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        # search query entry intentionally left blank

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_highlight_text_mode_with_specific_page_returns_panel_values(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Highlight text")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._search_query_entry.insert(0, "invoice")
        panel._search_page_entry.insert(0, "3")

        values = panel.collect()

        assert values.mode == "highlight_text"
        assert values.source == Path("in.pdf")
        assert values.output == Path("out.pdf")
        assert values.search_query == "invoice"
        assert values.page == 3

    def test_highlight_text_mode_with_blank_page_returns_none_for_all_pages(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Highlight text")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._search_query_entry.insert(0, "invoice")
        # search page entry intentionally left blank -> all pages

        values = panel.collect()

        assert values.mode == "highlight_text"
        assert values.search_query == "invoice"
        assert values.page is None

    def test_redact_text_mode_empty_search_query_raises(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Redact text")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        # search query entry intentionally left blank

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_redact_text_mode_with_specific_page_returns_panel_values(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Redact text")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._search_query_entry.insert(0, "confidential")
        panel._search_page_entry.insert(0, "1")

        values = panel.collect()

        assert values.mode == "redact_text"
        assert values.source == Path("in.pdf")
        assert values.output == Path("out.pdf")
        assert values.search_query == "confidential"
        assert values.page == 1

    def test_redact_text_mode_with_blank_page_returns_none_for_all_pages(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Redact text")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._search_query_entry.insert(0, "confidential")
        # search page entry intentionally left blank -> all pages

        values = panel.collect()

        assert values.mode == "redact_text"
        assert values.page is None
