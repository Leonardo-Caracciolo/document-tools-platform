"""Tests for `AdvancedEditorWindow`: the render-to-display pipeline (page
render, fit-to-window, in-window error state), the click-to-select span
hit-testing + highlight layer (slice 2 PR 1), and the
replace-then-rerender flow, the immutable-original/advancing-working-copy
state machine, Save As, and temp-dir cleanup (slice 2 PR 2).

Skips cleanly via `importorskip` when PySide6 is absent from the active
env (it is an optional dependency group — see `pyproject.toml`).

Any Qt widget construction requires a real `QApplication`; this module
runs it under `QT_QPA_PLATFORM=offscreen` so no window is actually
displayed, which keeps these tests runnable in headless CI. The env var
must be set BEFORE the first `QApplication` is constructed, which
happens in the module-scoped `qapp` fixture below.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pymupdf
import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QSize  # noqa: E402 - must follow importorskip/env setup
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel  # noqa: E402

from app.core.exceptions import EntradaInvalidaError, PDFCorruptoError  # noqa: E402
from app.core.services.pdf_service import PDFService, SpanInfo  # noqa: E402
from app.qt_editor.clickable_page_item import ClickablePageItem  # noqa: E402
from app.qt_editor.editor_window import AdvancedEditorWindow  # noqa: E402
from app.ui.errors import error_message  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """One real, offscreen `QApplication` shared across this module.

    Qt does not support constructing more than one `QApplication` per
    process; `QApplication.instance()` reuses an already-running one
    (e.g. if another test module in the same session created it first).
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestConstruction:
    def test_window_title_matches_pattern(
        self, qapp: QApplication, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")

        window = AdvancedEditorWindow(pdf_path, 1)

        assert window.windowTitle() == f"Advanced Editor — {pdf_path.name}"

    def test_render_happy_path_adds_pixmap_and_matches_scene_rect(
        self, qapp: QApplication, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")

        window = AdvancedEditorWindow(pdf_path, 1)

        assert window._pixmap_item is not None
        assert window._scene.sceneRect() == window._pixmap_item.boundingRect()


class TestFitGuard:
    def test_fit_skips_fitinview_when_viewport_size_is_non_positive(
        self, qapp: QApplication, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")
        window = AdvancedEditorWindow(pdf_path, 1)
        window._view.viewport().resize(QSize(0, 0))
        window._view.fitInView = MagicMock()

        window._fit()  # must not raise

        window._view.fitInView.assert_not_called()

    def test_fit_is_a_no_op_when_no_pixmap_was_ever_rendered(
        self, qapp: QApplication, corrupt_pdf_factory: Callable[..., Path]
    ) -> None:
        # A failed render leaves `_pixmap_item` as `None` — `_fit()` must
        # guard against this (first clause) and never raise, whether
        # called internally (showEvent/resizeEvent) or directly.
        pdf_path = corrupt_pdf_factory(name="corrupt.pdf")
        window = AdvancedEditorWindow(pdf_path, 1)
        assert window._pixmap_item is None

        window._fit()  # must not raise


class TestErrorHandling:
    def test_render_invalid_page_shows_error_label_instead_of_raising(
        self, qapp: QApplication, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf", pages=1)

        window = AdvancedEditorWindow(pdf_path, 99)  # out of range

        central = window.centralWidget()
        assert isinstance(central, QLabel)
        assert "Could not open this page." in central.text()
        # `_show_error` must route through the project's single
        # exception-to-message resolver (`app.ui.errors.error_message`),
        # never a raw exception message — this is `EntradaInvalidaError`'s
        # resolved text, not `str(exc)`.
        assert error_message(EntradaInvalidaError("anything")) in central.text()

    def test_render_corrupt_pdf_shows_error_label_instead_of_raising(
        self, qapp: QApplication, corrupt_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = corrupt_pdf_factory(name="corrupt.pdf")

        window = AdvancedEditorWindow(pdf_path, 1)

        central = window.centralWidget()
        assert isinstance(central, QLabel)
        assert "Could not open this page." in central.text()
        # Same resolver check for `PDFCorruptoError` — proves the routing
        # is not accidentally correct for only one of the two caught
        # exception types.
        assert error_message(PDFCorruptoError("anything")) in central.text()


def _point_to_pixel(window: AdvancedEditorWindow, point: tuple[float, float]) -> QPointF:
    """Forward-map a known PDF point to the item-local pixel `QPointF` a
    click there would produce, using the window's current `_zoom`/
    `_page_origin` — the exact inverse of `_on_canvas_click`'s own
    pixel-to-point mapping (`point = pixel / zoom + origin`, so
    `pixel = (point - origin) * zoom`). This inverse was round-trip
    verified against a real rendered page during design: mapping a known
    span's bbox to pixels and back landed on the identical span."""
    return QPointF(
        (point[0] - window._page_origin[0]) * window._zoom,
        (point[1] - window._page_origin[1]) * window._zoom,
    )


def _click_known_span(
    window: AdvancedEditorWindow, pdf_path: Path, page: int = 1
) -> tuple[dict, tuple[float, float]]:
    """Click `styled_text_pdf_factory`'s known span on `window` and
    return `(ground_truth_dict, click_point)` for reuse by callers that
    need to re-derive a nearby point on a since-edited working copy."""
    doc = pymupdf.open(pdf_path)
    try:
        ground_truth = doc.load_page(page - 1).get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
    finally:
        doc.close()
    bbox = ground_truth["bbox"]
    click_point = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    window._on_canvas_click(_point_to_pixel(window, click_point))
    return ground_truth, click_point


class TestClickToSelect:
    def test_initial_render_stores_zoom_and_origin(
        self, qapp: QApplication, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")

        window = AdvancedEditorWindow(pdf_path, 1)

        assert window._zoom > 0
        assert window._page_origin == (0.0, 0.0)
        assert isinstance(window._pixmap_item, ClickablePageItem)

    def test_click_hits_known_span_selects_and_highlights(
        self, qapp: QApplication, styled_text_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = styled_text_pdf_factory(
            "styled.pdf", text="Hello Span", point=(72, 100)
        )
        window = AdvancedEditorWindow(pdf_path, 1)
        doc = pymupdf.open(pdf_path)
        try:
            ground_truth = doc.load_page(0).get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
        finally:
            doc.close()
        bbox = ground_truth["bbox"]
        click_point = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        pixel = _point_to_pixel(window, click_point)

        window._on_canvas_click(pixel)

        assert window._selected_span is not None
        assert window._selected_span.text == ground_truth["text"]
        assert window._selected_label.text() == f"Selected: {ground_truth['text']!r}"
        assert window._highlight_item is not None
        assert window._highlight_item in window._scene.items()
        assert window._feedback.text() == ""

    def test_click_misses_clears_selection_and_shows_hint(
        self, qapp: QApplication, styled_text_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = styled_text_pdf_factory("styled.pdf", text="Hello Span", point=(72, 100))
        window = AdvancedEditorWindow(pdf_path, 1)
        pixel = _point_to_pixel(window, (500, 700))  # matches empty-space case in PDFService tests

        window._on_canvas_click(pixel)  # must not raise

        assert window._selected_span is None
        assert window._selected_label.text() == "Selected: (none)"
        assert window._highlight_item is None
        assert window._feedback.text() == "No text at that point — click directly on a word."

    def test_click_raising_entrada_invalida_shows_inline_feedback_no_crash(
        self,
        qapp: QApplication,
        valid_pdf_factory: Callable[..., Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")
        window = AdvancedEditorWindow(pdf_path, 1)
        exc = EntradaInvalidaError("boom")
        monkeypatch.setattr(
            PDFService, "find_span_at_point", MagicMock(side_effect=exc)
        )

        window._on_canvas_click(QPointF(10.0, 10.0))  # must not raise

        assert window._feedback.text() == error_message(exc)
        assert window._selected_span is None

    def test_click_raising_pdf_corrupto_shows_inline_feedback_no_crash(
        self,
        qapp: QApplication,
        valid_pdf_factory: Callable[..., Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")
        window = AdvancedEditorWindow(pdf_path, 1)
        exc = PDFCorruptoError("boom")
        monkeypatch.setattr(
            PDFService, "find_span_at_point", MagicMock(side_effect=exc)
        )

        window._on_canvas_click(QPointF(10.0, 10.0))  # must not raise

        assert window._feedback.text() == error_message(exc)
        assert window._selected_span is None

    def test_click_before_any_render_is_a_no_op(
        self, qapp: QApplication, corrupt_pdf_factory: Callable[..., Path]
    ) -> None:
        # Construction render failed (corrupt input), so `_zoom` stays at
        # its 0.0 default — the `_zoom <= 0` guard must prevent a
        # division-by-zero click mapping.
        pdf_path = corrupt_pdf_factory(name="corrupt.pdf")
        window = AdvancedEditorWindow(pdf_path, 1)
        assert window._zoom <= 0

        window._on_canvas_click(QPointF(10.0, 10.0))  # must not raise


class TestClearSelection:
    def test_clear_selection_resets_label_and_removes_highlight(
        self, qapp: QApplication, styled_text_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = styled_text_pdf_factory("styled.pdf", text="Hello Span", point=(72, 100))
        window = AdvancedEditorWindow(pdf_path, 1)
        doc = pymupdf.open(pdf_path)
        try:
            ground_truth = doc.load_page(0).get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
        finally:
            doc.close()
        bbox = ground_truth["bbox"]
        click_point = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        window._on_canvas_click(_point_to_pixel(window, click_point))
        assert window._selected_span is not None
        highlight_item = window._highlight_item
        assert highlight_item is not None

        window._clear_selection()

        assert window._selected_span is None
        assert window._selected_label.text() == "Selected: (none)"
        assert window._highlight_item is None
        assert highlight_item not in window._scene.items()


class TestClearFeedback:
    def test_clear_feedback_resets_label_to_empty(
        self, qapp: QApplication, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")
        window = AdvancedEditorWindow(pdf_path, 1)
        window._feedback.setText("some prior outcome message")

        window._clear_feedback()

        assert window._feedback.text() == ""

    def test_click_hit_after_a_prior_message_clears_it_via_clear_feedback(
        self, qapp: QApplication, styled_text_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = styled_text_pdf_factory("styled.pdf", text="Hello Span", point=(72, 100))
        window = AdvancedEditorWindow(pdf_path, 1)
        window._feedback.setText("a stale hint from a prior miss")

        _click_known_span(window, pdf_path)

        assert window._feedback.text() == ""


class TestReplaceFlow:
    def test_successful_replace_advances_pointer_and_rerenders(
        self, qapp: QApplication, styled_text_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = styled_text_pdf_factory("styled.pdf", text="Hello Span", point=(72, 100))
        window = AdvancedEditorWindow(pdf_path, 1)
        _click_known_span(window, pdf_path)
        assert window._selected_span is not None
        window._replacement_edit.setText("Renamed1")

        window._on_replace()

        assert window._working_path != pdf_path  # pointer advanced to the new edit's output
        assert window._working_path.parent == window._temp_dir
        assert window._working_path.exists()
        assert window._selected_span is None  # stale selection cleared
        assert window._selected_label.text() == "Selected: (none)"
        assert window._replacement_edit.text() == ""
        assert window._feedback.text() == "Replacement applied."
        doc = pymupdf.open(window._working_path)
        try:
            text = doc.load_page(0).get_text()
        finally:
            doc.close()
        assert "Hello Span" not in text
        assert "Renamed1" in text

    def test_failed_replace_preserves_prior_state(
        self,
        qapp: QApplication,
        styled_text_pdf_factory: Callable[..., Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_path = styled_text_pdf_factory("styled.pdf", text="Hello Span", point=(72, 100))
        window = AdvancedEditorWindow(pdf_path, 1)
        ground_truth, _ = _click_known_span(window, pdf_path)
        window._replacement_edit.setText("Renamed1")
        exc = EntradaInvalidaError("boom")
        monkeypatch.setattr(PDFService, "replace_text", MagicMock(side_effect=exc))

        window._on_replace()  # must not raise

        assert window._working_path == pdf_path  # pointer must NOT advance when replace_text raises
        assert window._selected_span is not None
        assert window._selected_span.text == ground_truth["text"]
        assert window._feedback.text() == error_message(exc)
        assert window._replacement_edit.text() == "Renamed1"  # preserved for retry

    def test_post_write_render_failure_does_not_advance_pointer(
        self,
        qapp: QApplication,
        styled_text_pdf_factory: Callable[..., Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_path = styled_text_pdf_factory("styled.pdf", text="Hello Span", point=(72, 100))
        # Construction's render uses the real, unpatched method.
        window = AdvancedEditorWindow(pdf_path, 1)
        _click_known_span(window, pdf_path)
        window._replacement_edit.setText("Renamed1")
        # Patched only AFTER construction: the only call reaching this
        # fake is the post-replace re-render triggered by `_on_replace`.
        monkeypatch.setattr(
            PDFService, "render_page", MagicMock(side_effect=PDFCorruptoError("boom"))
        )

        window._on_replace()  # must not raise

        # pointer must NOT advance when the post-write re-render fails,
        # even though the write itself succeeded
        assert window._working_path == pdf_path
        assert window._selected_span is not None  # early return, before _clear_selection

    def test_second_edit_sources_from_first_edits_output_not_original(
        self,
        qapp: QApplication,
        styled_text_pdf_factory: Callable[..., Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Covers two related scenarios as one chained-edit story: the
        first edit must source from the original file, and a second edit
        must source from the first edit's output (not the original) —
        proving edits chain onto each other rather than each restarting
        from `pdf_path`."""
        pdf_path = styled_text_pdf_factory("styled.pdf", text="Hello Span", point=(72, 100))
        window = AdvancedEditorWindow(pdf_path, 1)
        original_replace_text = PDFService.replace_text
        sources: list[Path] = []

        def spy_replace_text(self, source, output, page, span, replacement):  # noqa: ANN001
            sources.append(source)
            return original_replace_text(self, source, output, page, span, replacement)

        monkeypatch.setattr(PDFService, "replace_text", spy_replace_text)

        ground_truth, _ = _click_known_span(window, pdf_path)
        window._replacement_edit.setText("Renamed1")
        window._on_replace()
        first_output = window._working_path
        assert sources[0] == pdf_path  # first edit's source is the original file
        assert first_output != pdf_path

        # Re-select near the same spot on the since-edited working copy —
        # the replacement starts at the same span origin, so a point near
        # the bbox's left edge still lands inside the new (possibly
        # narrower) span regardless of the replacement text's length.
        bbox = ground_truth["bbox"]
        near_start = (bbox[0] + 2, (bbox[1] + bbox[3]) / 2)
        span = PDFService().find_span_at_point(window._working_path, 1, near_start)
        assert span is not None
        window._selected_span = span
        window._sync_replace_button()
        window._replacement_edit.setText("Renamed2")

        window._on_replace()

        # second edit's source is edit 1's output, not the original
        assert sources[1] == first_output
        assert sources[1] != pdf_path

    def test_original_file_remains_untouched_after_replacements(
        self, qapp: QApplication, styled_text_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = styled_text_pdf_factory("styled.pdf", text="Hello Span", point=(72, 100))
        original_bytes = pdf_path.read_bytes()
        window = AdvancedEditorWindow(pdf_path, 1)
        _click_known_span(window, pdf_path)
        window._replacement_edit.setText("Renamed1")

        window._on_replace()

        # original file must remain byte-for-byte untouched after edits
        assert pdf_path.read_bytes() == original_bytes

    def test_on_replace_does_not_call_service_when_no_selection(
        self,
        qapp: QApplication,
        valid_pdf_factory: Callable[..., Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")
        window = AdvancedEditorWindow(pdf_path, 1)
        assert window._selected_span is None
        mock_replace = MagicMock()
        monkeypatch.setattr(PDFService, "replace_text", mock_replace)

        # must not raise; the Replace button is normally gated by
        # _sync_replace_button, so this exercises the defensive
        # early-return when called directly with no selection
        window._on_replace()

        mock_replace.assert_not_called()
        assert window._working_path == pdf_path


class TestSyncReplaceButton:
    def test_replace_button_enabled_only_with_selection_and_non_empty_input(
        self, qapp: QApplication, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")
        window = AdvancedEditorWindow(pdf_path, 1)
        dummy_span = SpanInfo(
            text="x",
            bbox=(0, 0, 1, 1),
            origin=(0, 0),
            font="Helvetica",
            size=12.0,
            color=(0, 0, 0),
        )

        window._selected_span = None
        window._replacement_edit.setText("")
        window._sync_replace_button()
        assert window._replace_button.isEnabled() is False  # no selection, empty input

        window._selected_span = dummy_span
        window._replacement_edit.setText("")
        window._sync_replace_button()
        assert window._replace_button.isEnabled() is False  # selection, empty input

        window._selected_span = None
        window._replacement_edit.setText("hello")
        window._sync_replace_button()
        assert window._replace_button.isEnabled() is False  # no selection, non-empty input

        window._selected_span = dummy_span
        window._replacement_edit.setText("   ")
        window._sync_replace_button()
        assert window._replace_button.isEnabled() is False  # selection, whitespace-only input

        window._selected_span = dummy_span
        window._replacement_edit.setText("hello")
        window._sync_replace_button()
        assert window._replace_button.isEnabled() is True  # selection, non-empty input


class TestSaveAs:
    def test_user_confirms_save_copies_working_copy_to_destination(
        self,
        qapp: QApplication,
        valid_pdf_factory: Callable[..., Path],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")
        window = AdvancedEditorWindow(pdf_path, 1)
        dest = tmp_path / "chosen_output.pdf"
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            MagicMock(return_value=(str(dest), "PDF files (*.pdf)")),
        )

        window._on_save_as()

        assert dest.exists()
        assert dest.read_bytes() == window._working_path.read_bytes()
        assert window._working_path == pdf_path  # session state unchanged; may keep editing
        assert "Saved to" in window._feedback.text()

    def test_user_cancels_save_writes_nothing_and_changes_no_state(
        self,
        qapp: QApplication,
        valid_pdf_factory: Callable[..., Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")
        window = AdvancedEditorWindow(pdf_path, 1)
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", MagicMock(return_value=("", ""))
        )  # real getSaveFileName returns ('', '') on cancel, not None — mock matches that shape
        mock_copyfile = MagicMock()
        monkeypatch.setattr(shutil, "copyfile", mock_copyfile)

        window._on_save_as()  # must not raise

        mock_copyfile.assert_not_called()
        assert window._working_path == pdf_path

    def test_save_with_zero_edits_copies_current_original_content(
        self,
        qapp: QApplication,
        valid_pdf_factory: Callable[..., Path],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")
        window = AdvancedEditorWindow(pdf_path, 1)
        assert window._working_path == window._original_path  # no replace happened yet
        dest = tmp_path / "zero_edits_output.pdf"
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            MagicMock(return_value=(str(dest), "PDF files (*.pdf)")),
        )

        window._on_save_as()

        assert dest.read_bytes() == pdf_path.read_bytes()


class TestCloseEventCleanup:
    def test_close_wipes_temp_dir(
        self, qapp: QApplication, styled_text_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = styled_text_pdf_factory("styled.pdf", text="Hello Span", point=(72, 100))
        window = AdvancedEditorWindow(pdf_path, 1)
        _click_known_span(window, pdf_path)
        window._replacement_edit.setText("Renamed1")
        window._on_replace()
        assert window._temp_dir.exists()
        assert any(window._temp_dir.iterdir())

        window.close()

        # closeEvent must wipe the temp dir so no edit copies are left on
        # disk after the window closes
        assert not window._temp_dir.exists()
