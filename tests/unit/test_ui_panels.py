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

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import customtkinter as ctk
import pytest
from PIL import Image

from app.core.exceptions import EntradaInvalidaError, PDFCorruptoError
from app.core.services.pdf_service import PagePreviewResult, SpanInfo
from app.ui.registry import SecretField
from app.ui.widgets import panels as panels_module
from app.ui.widgets.panels import (
    EditPanel,
    MultiInSingleOutPanel,
    OrderPanel,
    SecretPanel,
    SingleInDirOutPanel,
    SingleInSingleOutPanel,
)


def _make_span(
    text: str = "Hello",
    bbox: tuple[float, float, float, float] = (10.0, 10.0, 50.0, 20.0),
) -> SpanInfo:
    return SpanInfo(
        text=text,
        bbox=bbox,
        origin=(10.0, 18.0),
        font="Helvetica",
        size=11.0,
        color=(0.0, 0.0, 0.0),
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

    # -- click-to-point / preset coexistence — `sdd/edit-pdf-preview/spec`
    # "Click-to-Point Capture" / "Preset-vs-Click Precedence" --

    def test_on_preview_point_sets_click_point_and_collect_returns_it(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._add_page_entry.insert(0, "2")
        panel._insert_text_entry.insert(0, "hello")

        panel._on_preview_point((123.0, 456.0))

        assert panel._click_point == (123.0, 456.0)
        assert panel._position_menu.get() == panels_module._CLICKED_POSITION_LABEL

        values = panel.collect()

        assert values.mode == "add_text"
        assert values.point == (123.0, 456.0)
        # `position` is a harmless placeholder here, not read/relied upon
        # by the caller — the point-wins contract is enforced service-side
        # (`PDFService.add_text` ignores `position` whenever `point` is
        # not `None`), not by this panel.
        assert values.position is not None

    def test_on_position_select_after_click_clears_click_point(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._add_page_entry.insert(0, "2")
        panel._insert_text_entry.insert(0, "hello")
        panel._on_preview_point((123.0, 456.0))

        # A real user dropdown pick updates the menu's own display before
        # `command=` fires — mirror that ordering directly, same as this
        # file's established `.set()`-doesn't-fire-`command` caveat for
        # `_mode_menu` (class docstring above).
        panel._position_menu.set("bottom-right")
        panel._on_position_select("bottom-right")

        assert panel._click_point is None

        values = panel.collect()

        assert values.point is None
        assert values.position == "bottom-right"

    # -- click-point invalidation on source/page/mode change — regression
    # for a review-caught bug where a stale point kept winning in
    # `collect()` after the page/document it was captured against was no
    # longer selected (`sdd/edit-pdf-preview/apply-progress`) --

    def test_on_source_change_clears_a_stale_click_point(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._add_page_entry.insert(0, "1")
        panel._insert_text_entry.insert(0, "hello")
        panel._on_preview_point((123.0, 456.0))
        assert panel._click_point == (123.0, 456.0)

        # A real SourceRow updates its own `_path` before invoking the
        # on_change callback — mirror that ordering directly.
        panel._source_row._path = Path("different.pdf")
        panel._on_source_change(Path("different.pdf"))

        assert panel._click_point is None
        assert panel._position_menu.get() == panels_module._DEFAULT_POSITION
        # Close the loop end-to-end, not just on internal state: a stale
        # point must not survive into collect()'s returned PanelValues.
        values = panel.collect()
        assert values.point is None
        assert values.position == panels_module._DEFAULT_POSITION

    def test_page_commit_clears_a_stale_click_point(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._add_page_entry.insert(0, "1")
        panel._insert_text_entry.insert(0, "hello")
        panel._on_preview_point((123.0, 456.0))
        assert panel._click_point == (123.0, 456.0)

        panel._refresh_preview_evt(event=None)

        assert panel._click_point is None
        assert panel._position_menu.get() == panels_module._DEFAULT_POSITION
        values = panel.collect()
        assert values.point is None
        assert values.position == panels_module._DEFAULT_POSITION

    def test_mode_change_away_and_back_clears_a_stale_click_point(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._add_page_entry.insert(0, "1")
        panel._insert_text_entry.insert(0, "hello")
        panel._on_preview_point((123.0, 456.0))
        assert panel._click_point == (123.0, 456.0)

        panel._on_mode_change("Highlight text")
        panel._on_mode_change("Add text")

        assert panel._click_point is None
        assert panel._position_menu.get() == panels_module._DEFAULT_POSITION
        values = panel.collect()
        assert values.point is None
        assert values.position == panels_module._DEFAULT_POSITION

    # -- `_refresh_preview` graceful degradation — `sdd/edit-pdf-preview/spec`
    # "Preview Rendering Graceful Degradation" --

    def test_refresh_preview_shows_placeholder_when_source_is_none(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")

        panel._refresh_preview()

        assert panel._preview._ctk_image is None

    def test_refresh_preview_shows_placeholder_when_page_is_blank(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        # page entry intentionally left blank

        panel._refresh_preview()

        assert panel._preview._ctk_image is None

    def test_refresh_preview_shows_placeholder_when_page_is_invalid(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._add_page_entry.insert(0, "not-a-number")

        panel._refresh_preview()

        assert panel._preview._ctk_image is None

    def test_refresh_preview_shows_placeholder_when_render_page_raises_entrada_invalida(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = MagicMock(name="PDFService")
        mock_cls.return_value.render_page.side_effect = EntradaInvalidaError("out of range")
        monkeypatch.setattr(panels_module, "PDFService", mock_cls)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._add_page_entry.insert(0, "99")

        panel._refresh_preview()

        assert panel._preview._ctk_image is None

    def test_refresh_preview_shows_placeholder_when_render_page_raises_pdf_corrupto(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = MagicMock(name="PDFService")
        mock_cls.return_value.render_page.side_effect = PDFCorruptoError("corrupt")
        monkeypatch.setattr(panels_module, "PDFService", mock_cls)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._add_page_entry.insert(0, "2")

        panel._refresh_preview()

        assert panel._preview._ctk_image is None

    def test_refresh_preview_shows_render_result_on_success(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = PagePreviewResult(
            image=Image.new("RGB", (100, 140), color="white"),
            zoom=0.5,
            origin=(0.0, 0.0),
        )
        mock_cls = MagicMock(name="PDFService")
        mock_cls.return_value.render_page.return_value = result
        monkeypatch.setattr(panels_module, "PDFService", mock_cls)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")
        panel._add_page_entry.insert(0, "2")

        panel._refresh_preview()

        assert panel._preview._ctk_image is not None
        assert panel._preview._zoom == 0.5
        mock_cls.return_value.render_page.assert_called_once_with(
            Path("in.pdf"), 2, panels_module._PREVIEW_MAX_W, panels_module._PREVIEW_MAX_H
        )


class TestEditPanelReplaceMode:
    """`sdd/edit-pdf-replace-text/spec` — "Replace-Mode Field Visibility and
    Local Guard", "Visual Selection Overlay", "Empty-Space Click Graceful
    Degradation", "_selected_span Invalidation".
    """

    # -- mode visibility --

    def test_on_mode_change_to_replace_text_shows_only_replace_group_fields(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")

        panel._on_mode_change("Replace text")

        assert panel._mode == "replace_text"
        assert panel._replace_page_entry.grid_info() != {}
        assert panel._replacement_entry.grid_info() != {}
        assert panel._selection_feedback.grid_info() != {}
        assert panel._add_page_entry.grid_info() == {}
        assert panel._insert_text_entry.grid_info() == {}
        assert panel._position_menu.grid_info() == {}
        assert panel._search_query_entry.grid_info() == {}
        assert panel._search_page_entry.grid_info() == {}

    def test_on_mode_change_away_from_replace_text_hides_replace_group(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")

        panel._on_mode_change("Add text")

        assert panel._replace_page_entry.grid_info() == {}
        assert panel._replacement_entry.grid_info() == {}
        assert panel._selection_feedback.grid_info() == {}

    # -- collect() guard --

    def test_collect_raises_without_selected_span_and_makes_no_service_call(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = MagicMock(name="PDFService")
        monkeypatch.setattr(panels_module, "PDFService", mock_cls)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._replace_page_entry.insert(0, "1")
        panel._replacement_entry.insert(0, "new text")
        # _selected_span intentionally left None

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

        mock_cls.return_value.replace_text.assert_not_called()

    def test_collect_raises_on_blank_replacement(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._replace_page_entry.insert(0, "1")
        panel._selected_span = _make_span()
        # replacement entry intentionally left blank

        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_collect_returns_panel_values_when_span_and_replacement_set(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._replace_page_entry.insert(0, "2")
        panel._replacement_entry.insert(0, "new text")
        span = _make_span()
        panel._selected_span = span

        values = panel.collect()

        assert values.mode == "replace_text"
        assert values.source == Path("in.pdf")
        assert values.output == Path("out.pdf")
        assert values.page == 2
        assert values.selected_span is span
        assert values.replacement == "new text"

    # -- `_on_preview_point` --

    def test_on_preview_point_add_text_mode_also_marks_point(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._preview.mark_point = MagicMock()

        panel._on_preview_point((5.0, 5.0))

        panel._preview.mark_point.assert_called_once_with((5.0, 5.0))

    def test_on_preview_point_hit_sets_span_marks_and_feedback(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        span = _make_span(text="Hello Span")
        mock_cls = MagicMock(name="PDFService")
        mock_cls.return_value.find_span_at_point.return_value = span
        monkeypatch.setattr(panels_module, "PDFService", mock_cls)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._replace_page_entry.insert(0, "1")
        panel._preview.mark_span = MagicMock()

        panel._on_preview_point((15.0, 15.0))

        assert panel._selected_span is span
        panel._preview.mark_span.assert_called_once_with(span.bbox)
        assert panel._selection_feedback.cget("text") == "Selected: 'Hello Span'"
        mock_cls.return_value.find_span_at_point.assert_called_once_with(
            Path("in.pdf"), 1, (15.0, 15.0)
        )

    def test_on_preview_point_miss_clears_span_and_shows_message(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = MagicMock(name="PDFService")
        mock_cls.return_value.find_span_at_point.return_value = None
        monkeypatch.setattr(panels_module, "PDFService", mock_cls)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._replace_page_entry.insert(0, "1")
        panel._selected_span = _make_span()
        panel._preview.clear_marks = MagicMock()

        panel._on_preview_point((500.0, 700.0))

        assert panel._selected_span is None
        panel._preview.clear_marks.assert_called_once()
        assert panel._selection_feedback.cget("text") == panels_module._NO_SPAN_AT_CLICK_MESSAGE

    def test_on_preview_point_no_source_shows_message_without_crash(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._replace_page_entry.insert(0, "1")
        # source intentionally left unset

        panel._on_preview_point((10.0, 10.0))  # must not raise

        assert panel._selected_span is None
        assert panel._selection_feedback.cget("text") == panels_module._NO_SPAN_AT_CLICK_MESSAGE

    def test_on_preview_point_invalid_page_shows_message_without_crash(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        # page entry intentionally left blank -> invalid for replace mode

        panel._on_preview_point((10.0, 10.0))  # must not raise

        assert panel._selected_span is None
        assert panel._selection_feedback.cget("text") == panels_module._NO_SPAN_AT_CLICK_MESSAGE

    def test_on_preview_point_translates_pdf_corrupto_to_inline_message(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = MagicMock(name="PDFService")
        mock_cls.return_value.find_span_at_point.side_effect = PDFCorruptoError("corrupt")
        monkeypatch.setattr(panels_module, "PDFService", mock_cls)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._replace_page_entry.insert(0, "1")

        panel._on_preview_point((10.0, 10.0))  # must not raise

        assert panel._selected_span is None
        assert panel._selection_feedback.cget("text") == panels_module._NO_SPAN_AT_CLICK_MESSAGE

    # -- `_selected_span` invalidation on source/page/mode change — direct
    # template: `TestEditPanel`'s existing `_click_point` invalidation
    # tests above (`test_on_source_change_clears_a_stale_click_point`,
    # `test_page_commit_clears_a_stale_click_point`,
    # `test_mode_change_away_and_back_clears_a_stale_click_point`).
    # Asserted through `collect()`'s actual return value, not just
    # internal state — the same directness gap a follow-up commit had to
    # fix after `edit-pdf-preview`'s own verify pass.

    def test_on_source_change_clears_a_stale_selected_span(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._save_as_row._output = Path("out.pdf")
        panel._replace_page_entry.insert(0, "1")
        panel._replacement_entry.insert(0, "new text")
        panel._selected_span = _make_span()
        assert panel._selected_span is not None

        panel._source_row._path = Path("different.pdf")
        panel._on_source_change(Path("different.pdf"))

        assert panel._selected_span is None
        assert panel._selection_feedback.cget("text") == ""
        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_page_commit_clears_a_stale_selected_span(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._replace_page_entry.insert(0, "1")
        panel._replacement_entry.insert(0, "new text")
        panel._selected_span = _make_span()
        assert panel._selected_span is not None

        panel._refresh_preview_evt(event=None)

        assert panel._selected_span is None
        assert panel._selection_feedback.cget("text") == ""
        with pytest.raises(EntradaInvalidaError):
            panel.collect()

    def test_mode_change_away_and_back_clears_a_stale_selected_span(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._save_as_row._output = Path("out.pdf")
        panel._replace_page_entry.insert(0, "1")
        panel._replacement_entry.insert(0, "new text")
        panel._selected_span = _make_span()
        assert panel._selected_span is not None

        panel._on_mode_change("Add text")
        panel._on_mode_change("Replace text")

        assert panel._selected_span is None
        assert panel._selection_feedback.cget("text") == ""
        with pytest.raises(EntradaInvalidaError):
            panel.collect()


class TestEditPanelAdvancedEditor:
    """`sdd/qt-advanced-editor-slice1/spec` — "Advanced Editor Button
    Visibility", "Button Enablement Gated by Source Selection", "PySide6
    Pre-Launch Availability Check", "Fire-and-Forget Subprocess Launch",
    "Active Page Resolution", "Launch OSError Defense".

    `find_spec` and `Popen` are ALWAYS mocked in every launch test here —
    a real Qt process must never be spawned by this suite
    (`sdd/qt-advanced-editor-slice1/design` Testing Strategy).
    """

    # -- visibility --

    def test_button_and_feedback_gridded_only_in_replace_text_mode(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")

        assert panel._advanced_editor_button.grid_info() == {}
        assert panel._advanced_editor_feedback.grid_info() == {}

        panel._on_mode_change("Replace text")

        assert panel._advanced_editor_button.grid_info() != {}
        assert panel._advanced_editor_feedback.grid_info() != {}

    def test_button_and_feedback_absent_in_highlight_and_redact_modes(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")

        panel._on_mode_change("Highlight text")
        assert panel._advanced_editor_button.grid_info() == {}
        assert panel._advanced_editor_feedback.grid_info() == {}

        panel._on_mode_change("Redact text")
        assert panel._advanced_editor_button.grid_info() == {}
        assert panel._advanced_editor_feedback.grid_info() == {}

    def test_leaving_replace_text_mode_hides_button_and_feedback(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")

        panel._on_mode_change("Add text")

        assert panel._advanced_editor_button.grid_info() == {}
        assert panel._advanced_editor_feedback.grid_info() == {}

    # -- enablement gated by source selection --

    def test_button_starts_disabled(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")

        assert panel._advanced_editor_button.cget("state") == "disabled"

    def test_entering_replace_text_mode_without_source_keeps_button_disabled(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")

        panel._on_mode_change("Replace text")

        assert panel._advanced_editor_button.cget("state") == "disabled"

    def test_entering_replace_text_mode_with_source_already_set_enables_button(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._source_row._path = Path("in.pdf")

        panel._on_mode_change("Replace text")

        assert panel._advanced_editor_button.cget("state") == "normal"

    def test_on_source_change_enables_button_while_in_replace_text_mode(
        self, tk_root: ctk.CTk
    ) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        assert panel._advanced_editor_button.cget("state") == "disabled"

        panel._source_row._path = Path("in.pdf")
        panel._on_source_change(Path("in.pdf"))

        assert panel._advanced_editor_button.cget("state") == "normal"

    def test_on_source_change_to_none_disables_button(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._on_source_change(Path("in.pdf"))
        assert panel._advanced_editor_button.cget("state") == "normal"

        panel._source_row._path = None
        panel._on_source_change(None)

        assert panel._advanced_editor_button.cget("state") == "disabled"

    # -- `_resolve_launch_page` --

    def test_resolve_launch_page_blank_entry_defaults_to_one(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        # replace page entry intentionally left blank

        assert panel._resolve_launch_page() == 1

    def test_resolve_launch_page_invalid_entry_defaults_to_one(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._replace_page_entry.insert(0, "not-a-number")

        assert panel._resolve_launch_page() == 1

    def test_resolve_launch_page_valid_entry_is_parsed(self, tk_root: ctk.CTk) -> None:
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._replace_page_entry.insert(0, "7")

        assert panel._resolve_launch_page() == 7

    # -- `_launch_advanced_editor` --

    def test_launch_with_no_source_is_a_defensive_no_op(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_popen = MagicMock(name="Popen")
        monkeypatch.setattr(panels_module.subprocess, "Popen", mock_popen)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        # source intentionally left unset

        panel._launch_advanced_editor()  # must not raise

        mock_popen.assert_not_called()
        assert panel._advanced_editor_feedback.cget("text") == ""

    def test_launch_missing_pyside6_shows_message_and_does_not_spawn(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_find_spec = MagicMock(name="find_spec", return_value=None)
        mock_popen = MagicMock(name="Popen")
        monkeypatch.setattr(panels_module.importlib.util, "find_spec", mock_find_spec)
        monkeypatch.setattr(panels_module.subprocess, "Popen", mock_popen)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._on_source_change(Path("in.pdf"))

        panel._launch_advanced_editor()

        mock_find_spec.assert_called_once_with("PySide6")
        mock_popen.assert_not_called()
        assert (
            panel._advanced_editor_feedback.cget("text") == panels_module._PYSIDE6_MISSING_MESSAGE
        )

    def test_launch_happy_path_spawns_detached_process_and_clears_feedback(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_find_spec = MagicMock(name="find_spec", return_value=object())
        mock_popen = MagicMock(name="Popen")
        monkeypatch.setattr(panels_module.importlib.util, "find_spec", mock_find_spec)
        monkeypatch.setattr(panels_module.subprocess, "Popen", mock_popen)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._on_source_change(Path("in.pdf"))
        panel._replace_page_entry.insert(0, "3")
        panel._advanced_editor_feedback.configure(text="stale message")

        panel._launch_advanced_editor()

        mock_popen.assert_called_once_with(
            [sys.executable, "-m", "app.qt_editor", str(Path("in.pdf")), "--page", "3"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert panel._advanced_editor_feedback.cget("text") == ""

    def test_launch_blank_page_entry_falls_back_to_page_one(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_find_spec = MagicMock(name="find_spec", return_value=object())
        mock_popen = MagicMock(name="Popen")
        monkeypatch.setattr(panels_module.importlib.util, "find_spec", mock_find_spec)
        monkeypatch.setattr(panels_module.subprocess, "Popen", mock_popen)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._on_source_change(Path("in.pdf"))
        # replace page entry intentionally left blank

        panel._launch_advanced_editor()

        mock_popen.assert_called_once_with(
            [sys.executable, "-m", "app.qt_editor", str(Path("in.pdf")), "--page", "1"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def test_launch_popen_oserror_shows_failure_message_without_raising(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_find_spec = MagicMock(name="find_spec", return_value=object())
        mock_popen = MagicMock(name="Popen", side_effect=OSError("boom"))
        monkeypatch.setattr(panels_module.importlib.util, "find_spec", mock_find_spec)
        monkeypatch.setattr(panels_module.subprocess, "Popen", mock_popen)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._on_source_change(Path("in.pdf"))

        panel._launch_advanced_editor()  # must not raise

        assert (
            panel._advanced_editor_feedback.cget("text")
            == panels_module._ADVANCED_EDITOR_LAUNCH_FAILED_MESSAGE
        )

    # -- regression guard: `_selection_feedback` isolation (design D6) --

    def test_launch_success_does_not_touch_selection_feedback(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_find_spec = MagicMock(name="find_spec", return_value=object())
        mock_popen = MagicMock(name="Popen")
        monkeypatch.setattr(panels_module.importlib.util, "find_spec", mock_find_spec)
        monkeypatch.setattr(panels_module.subprocess, "Popen", mock_popen)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._on_source_change(Path("in.pdf"))
        panel._selection_feedback.configure(text="Selected: 'kept as-is'")

        panel._launch_advanced_editor()

        assert panel._selection_feedback.cget("text") == "Selected: 'kept as-is'"

    def test_launch_failure_does_not_touch_selection_feedback(
        self, tk_root: ctk.CTk, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_find_spec = MagicMock(name="find_spec", return_value=None)
        mock_popen = MagicMock(name="Popen")
        monkeypatch.setattr(panels_module.importlib.util, "find_spec", mock_find_spec)
        monkeypatch.setattr(panels_module.subprocess, "Popen", mock_popen)
        panel = EditPanel(tk_root, output_suffix="_edited", output_ext=".pdf")
        panel._on_mode_change("Replace text")
        panel._source_row._path = Path("in.pdf")
        panel._on_source_change(Path("in.pdf"))
        panel._selection_feedback.configure(text="Selected: 'kept as-is'")

        panel._launch_advanced_editor()

        assert panel._selection_feedback.cget("text") == "Selected: 'kept as-is'"
