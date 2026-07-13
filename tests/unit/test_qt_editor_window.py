"""Tests for `AdvancedEditorWindow` — `sdd/qt-advanced-editor-slice1/design`
"editor_window.py — render-to-display pipeline" (D2, D9).

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

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize  # noqa: E402 - must follow importorskip/env setup
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app.qt_editor.editor_window import AdvancedEditorWindow  # noqa: E402


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

    def test_render_corrupt_pdf_shows_error_label_instead_of_raising(
        self, qapp: QApplication, corrupt_pdf_factory: Callable[..., Path]
    ) -> None:
        pdf_path = corrupt_pdf_factory(name="corrupt.pdf")

        window = AdvancedEditorWindow(pdf_path, 1)

        central = window.centralWidget()
        assert isinstance(central, QLabel)
        assert "Could not open this page." in central.text()
