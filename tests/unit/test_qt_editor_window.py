"""Tests for `AdvancedEditorWindow` — `sdd/qt-advanced-editor-slice1/design`
"editor_window.py — render-to-display pipeline" (D2, D9) and
`sdd/qt-advanced-editor-slice2/design` click-to-select + highlight layer
(D1-D3, D9; slice 2 PR 1 scope only — no Replace/Save/state-machine yet).

Skips cleanly via `importorskip` when PySide6 is absent from the active
env (it is an optional dependency group — see `pyproject.toml`).

Any Qt widget construction requires a real `QApplication`; this module
runs it under `QT_QPA_PLATFORM=offscreen` so no window is actually
displayed, matching the design's Testing Strategy note for headless
runs. The env var must be set BEFORE the first `QApplication` is
constructed, which happens in the module-scoped `qapp` fixture below.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pymupdf
import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QSize  # noqa: E402 - must follow importorskip/env setup
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app.core.exceptions import EntradaInvalidaError, PDFCorruptoError  # noqa: E402
from app.core.services.pdf_service import PDFService  # noqa: E402
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
    `_origin` — the exact inverse of `_on_canvas_click`'s own mapping
    (`sdd/qt-advanced-editor-slice2/design` D2, E4-confirmed round-trip)."""
    return QPointF(
        (point[0] - window._origin[0]) * window._zoom,
        (point[1] - window._origin[1]) * window._zoom,
    )


class TestClickToSelect:
    def test_initial_render_stores_zoom_and_origin(
        self, qapp: QApplication, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = valid_pdf_factory(name="sample.pdf")

        window = AdvancedEditorWindow(pdf_path, 1)

        assert window._zoom > 0
        assert window._origin == (0.0, 0.0)
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
