"""`AdvancedEditorWindow` — the render-to-display pipeline for the
standalone PySide6 "Advanced Editor" process.

Owns render-box sizing, the `PDFService.render_page` -> `QPixmap` ->
`QGraphicsScene` pipeline, fit-to-window behavior, and an in-window error
state. See `sdd/qt-advanced-editor-slice1/design` for the full pipeline
rationale (D2, D9) and the empirical verification behind `_fit()`'s
viewport-size guard (V1).

Read-only: no editing, no save, no IPC to the parent process (slice 1
non-requirements).

`_render`'s in-window error state funnels through the same
`app.ui.errors.error_message()` resolver the Tkinter surfaces use
(`app/ui/widgets/panels.py`'s module docstring) — `error_message()` has
zero customtkinter/tkinter dependency, so importing it here does not
compromise this package's own zero-Tk-at-import-time goal.
"""

from __future__ import annotations

from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QMainWindow,
)

from app.core.exceptions import EntradaInvalidaError, PDFCorruptoError
from app.core.services.pdf_service import PDFService
from app.ui.errors import error_message

_FALLBACK_MAX_W = 1000
_FALLBACK_MAX_H = 1300
#: Fraction of the primary screen's available geometry the render box
#: targets — see `_render_box`.
_SCREEN_FRACTION = 0.9


class AdvancedEditorWindow(QMainWindow):
    """Read-only render+display window for a single PDF page.

    `pdf_path`/`page` are rendered once at construction via the
    already-shipped `PDFService().render_page`. No text or graphic
    element is editable — this window only ever displays a `QPixmap`.
    """

    def __init__(self, pdf_path: Path, page: int) -> None:
        super().__init__()
        self.setWindowTitle(f"Advanced Editor — {pdf_path.name}")
        self.setMinimumSize(800, 600)
        self._scene = QGraphicsScene(self)
        self._view = QGraphicsView(self._scene, self)
        self.setCentralWidget(self._view)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._render(pdf_path, page)

    def _render_box(self) -> tuple[int, int]:
        """Return the (max_w, max_h) render box: `_SCREEN_FRACTION` of the
        primary screen's available geometry, or the fallback box when no
        screen is available or the computed box is non-positive."""
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            w = int(avail.width() * _SCREEN_FRACTION)
            h = int(avail.height() * _SCREEN_FRACTION)
            if w > 0 and h > 0:
                return w, h
        return _FALLBACK_MAX_W, _FALLBACK_MAX_H

    def _render(self, pdf_path: Path, page: int) -> None:
        max_w, max_h = self._render_box()
        try:
            result = PDFService().render_page(pdf_path, page, max_w, max_h)
        except (EntradaInvalidaError, PDFCorruptoError) as exc:
            self._show_error(error_message(exc))
            return
        qimage = ImageQt(result.image)
        pixmap = QPixmap.fromImage(qimage)
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(self._pixmap_item.boundingRect())
        self._fit()

    def _fit(self) -> None:
        if self._pixmap_item is None:
            return
        vp = self._view.viewport().size()
        if vp.width() <= 0 or vp.height() <= 0:
            # V1: a spurious resizeEvent fires during window construction,
            # before showEvent, with a not-yet-realized viewport. Skip the
            # wasted fitInView call; showEvent's own fit supersedes it.
            return
        self._view.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override naming
        super().showEvent(event)
        self._fit()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override naming
        super().resizeEvent(event)
        self._fit()

    def _show_error(self, message: str) -> None:
        self.setCentralWidget(QLabel(f"Could not open this page.\n\n{message}"))
