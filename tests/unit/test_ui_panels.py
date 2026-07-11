"""Tests for the 5 family input panels' `collect()` guard paths — PR2 scope.

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
"""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk
import pytest

from app.core.exceptions import EntradaInvalidaError
from app.ui.registry import SecretField
from app.ui.widgets.panels import (
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
