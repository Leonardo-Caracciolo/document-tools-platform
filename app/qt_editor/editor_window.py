"""`AdvancedEditorWindow` — the render-to-display pipeline for the
standalone PySide6 "Advanced Editor" process.

Owns render-box sizing, the `PDFService.render_page` -> `QPixmap` ->
`QGraphicsScene` pipeline, fit-to-window behavior, in-window error state,
click-to-select span hit-testing with a highlight overlay, the
replace-then-rerender flow, the immutable-original/advancing-working-copy
state machine, and Save As.

Design history (`sdd/qt-advanced-editor-slice1/design`,
`sdd/qt-advanced-editor-slice2/design`) — summarized here so this module
stands on its own without that external record:
- The base render pipeline reuses the already-shipped
  `PDFService.render_page` verbatim (no new service method) and refreshes
  `self._zoom`/`self._page_origin` on every render, not just the first, so
  click-to-PDF-point mapping never drifts after an edit re-renders the
  page.
- `_fit()`'s viewport-size guard exists because a spurious `resizeEvent`
  fires during window construction, before `showEvent`, with a
  not-yet-realized (near-zero) viewport; fitting against that would
  compute a garbage scale that gets silently overwritten a moment later
  by `showEvent`'s own fit — the guard just skips that wasted, misleading
  call.
- The click/highlight layer (added in slice 2 PR 1) hit-tests via
  `ClickablePageItem`'s forwarded click and draws a separate overlay
  rect rather than redrawing the pixmap, so selection state stays cheap.
- The replace/save/state-machine layer (added in slice 2 PR 2) is the
  mutation surface described in the class docstring below.

Slice 2 PR 1 added click-to-select + highlight only. Slice 2 PR 2 (this
revision) adds the mutation surface: the replacement input + Replace
button, the replace-then-rerender flow, the working-copy pointer, temp-dir
lifecycle + `closeEvent` cleanup, and Save As. No IPC to the parent
process — that remains out of scope for this slice.

`_render`'s in-window error state funnels through the same
`app.ui.errors.error_message()` resolver the Tkinter surfaces use
(`app/ui/widgets/panels.py`'s module docstring) — `error_message()` has
zero customtkinter/tkinter dependency, so importing it here does not
compromise this package's own zero-Tk-at-import-time goal.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.exceptions import EntradaInvalidaError, PDFCorruptoError
from app.core.services.pdf_service import PDFService, SpanInfo
from app.qt_editor.clickable_page_item import ClickablePageItem
from app.ui.errors import error_message
from app.ui.registry import suggest_output_name

_FALLBACK_MAX_W = 1000
_FALLBACK_MAX_H = 1300
#: Fraction of the primary screen's available geometry the render box
#: targets — see `_render_box`.
_SCREEN_FRACTION = 0.9


class AdvancedEditorWindow(QMainWindow):
    """Click-to-select, replace-in-place editor window for a single PDF page.

    `pdf_path`/`page` are rendered via the already-shipped
    `PDFService().render_page`. Clicking the rendered page hit-tests a
    text span via `PDFService().find_span_at_point`; a hit is highlighted
    and its text shown in the side panel's "Selected" label. Typing a
    replacement and pressing Replace calls `PDFService().replace_text`
    against the current working copy and re-renders in place; the
    working-copy pointer only advances once both the write and the
    re-render succeed (never-corrupt invariant — see `_on_replace`).
    Save As copies the current working copy to a user-chosen destination.
    """

    def __init__(self, pdf_path: Path, page: int) -> None:
        super().__init__()
        self.setWindowTitle(f"Advanced Editor — {pdf_path.name}")
        self.setMinimumSize(900, 640)
        self._page = page
        self._original_path = pdf_path  # IMMUTABLE — never written
        # First edit's basis is the original file itself — no upfront copy.
        # replace_text never mutates its source, so pointing directly at
        # the original is safe.
        self._working_path = pdf_path
        # Each edit gets its own temp file here (see _next_temp_path); the
        # whole directory is wiped in closeEvent() when the window closes.
        self._temp_dir = Path(tempfile.mkdtemp(prefix="qt_editor_"))
        self._edit_seq = 0
        # Refreshed on every render (initial + every post-replace
        # re-render) so pixel-to-PDF-point click mapping never uses a
        # stale scale.
        self._zoom = 0.0
        self._page_origin: tuple[float, float] = (0.0, 0.0)
        self._selected_span: SpanInfo | None = None
        self._pixmap_item: ClickablePageItem | None = None
        # QGraphicsRectItem | None — a separate overlay rect drawn over
        # the selected span, not part of the pixmap itself, so
        # selecting/clearing the highlight never requires a re-render.
        self._highlight_item = None

        self._scene = QGraphicsScene(self)
        self._view = QGraphicsView(self._scene, self)
        self._build_central()
        self._render(self._working_path, terminal_on_error=True)

    def _build_central(self) -> None:
        panel = QWidget(self)
        col = QVBoxLayout(panel)
        self._selected_label = QLabel("Selected: (none)")
        self._replacement_edit = QLineEdit()
        self._replace_button = QPushButton("Replace")
        # Disabled until a span is selected AND replacement text is
        # entered — see _sync_replace_button.
        self._replace_button.setEnabled(False)
        self._save_button = QPushButton("Save As…")
        # Dedicated label for outcome messages (click-miss hint, replace
        # error/success) — kept separate from _selected_label so the two
        # concerns never overwrite each other.
        self._feedback = QLabel("")
        for w in (
            self._selected_label,
            self._replacement_edit,
            self._replace_button,
            self._save_button,
            self._feedback,
        ):
            col.addWidget(w)
        col.addStretch(1)
        self._replacement_edit.textChanged.connect(self._sync_replace_button)
        self._replace_button.clicked.connect(self._on_replace)
        self._save_button.clicked.connect(self._on_save_as)

        container = QWidget(self)
        row = QHBoxLayout(container)
        row.addWidget(self._view, stretch=1)
        row.addWidget(panel, stretch=0)
        self.setCentralWidget(container)

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

    def _render(self, source: Path, *, terminal_on_error: bool) -> bool:
        """Render `source`@`self._page` into the scene. Returns True on success.

        On failure: `terminal_on_error=True` (construction) -> full-window
        `_show_error` swap; `False` (post-replace, PR 2) -> inline
        feedback, scene/pointer untouched. `render_page`'s except runs
        before any scene mutation, so a `False` return leaves the old
        page visible.
        """
        max_w, max_h = self._render_box()
        try:
            result = PDFService().render_page(source, self._page, max_w, max_h)
        except (EntradaInvalidaError, PDFCorruptoError) as exc:
            if terminal_on_error:
                self._show_error(error_message(exc))
            else:
                self._feedback.setText(error_message(exc))
            return False
        # Refresh EVERY render — a post-replace re-render must overwrite
        # stale zoom/origin, or the next click maps to the wrong point.
        self._zoom = result.zoom
        self._page_origin = result.origin
        pixmap = QPixmap.fromImage(ImageQt(result.image))
        if self._pixmap_item is None:
            self._pixmap_item = ClickablePageItem(pixmap, on_click=self._on_canvas_click)
            self._scene.addItem(self._pixmap_item)  # addItem (not addPixmap) delivers clicks
        else:
            self._pixmap_item.setPixmap(pixmap)  # in-place swap keeps the click handler alive
        self._scene.setSceneRect(self._pixmap_item.boundingRect())
        self._fit()
        return True

    def _on_canvas_click(self, pos: QPointF) -> None:
        if self._zoom <= 0:
            return  # nothing rendered yet
        point = (
            pos.x() / self._zoom + self._page_origin[0],
            pos.y() / self._zoom + self._page_origin[1],
        )
        try:
            span = PDFService().find_span_at_point(self._working_path, self._page, point)
        except (EntradaInvalidaError, PDFCorruptoError) as exc:
            # Defensive: the basis just rendered ok, so this should not
            # normally happen, but never let a click crash the window.
            self._feedback.setText(error_message(exc))
            return
        if span is None:
            # A miss is a normal result, never an exception.
            self._clear_selection()
            self._feedback.setText("No text at that point — click directly on a word.")
            return
        self._selected_span = span
        self._selected_label.setText(f"Selected: {span.text!r}")
        self._draw_highlight(span.bbox)
        self._clear_feedback()
        self._sync_replace_button()

    def _draw_highlight(self, bbox: tuple[float, float, float, float]) -> None:
        if self._highlight_item is not None:
            self._scene.removeItem(self._highlight_item)
        x0 = (bbox[0] - self._page_origin[0]) * self._zoom
        y0 = (bbox[1] - self._page_origin[1]) * self._zoom
        x1 = (bbox[2] - self._page_origin[0]) * self._zoom
        y1 = (bbox[3] - self._page_origin[1]) * self._zoom
        self._highlight_item = self._scene.addRect(x0, y0, x1 - x0, y1 - y0)

    def _clear_selection(self) -> None:
        self._selected_span = None
        self._selected_label.setText("Selected: (none)")
        if self._highlight_item is not None:
            self._scene.removeItem(self._highlight_item)
            self._highlight_item = None
        self._sync_replace_button()

    def _clear_feedback(self) -> None:
        """Reset the outcome-message label to empty.

        Mirrors `_clear_selection()`: a single, obvious place for future
        handlers to route through whenever a prior outcome message (a
        click-miss hint, a replace error) is no longer relevant — as
        opposed to setting a brand-new message, which call sites do
        directly via `self._feedback.setText(...)`.
        """
        self._feedback.setText("")

    def _sync_replace_button(self) -> None:
        """Enable Replace only when a span is selected AND the input is
        non-empty (whitespace-only counts as empty)."""
        ready = self._selected_span is not None and bool(self._replacement_edit.text().strip())
        self._replace_button.setEnabled(ready)

    def _next_temp_path(self) -> Path:
        self._edit_seq += 1
        return self._temp_dir / f"edit_{self._edit_seq}.pdf"

    def _on_replace(self) -> None:
        """Replace `self._selected_span` with the input text against the
        current working copy, then re-render. The working-copy pointer
        advances ONLY once both the write and the re-render succeed —
        any failure leaves the pointer, the displayed page, and the
        selection exactly as they were, with an inline
        `error_message()`, never a crash (never-corrupt invariant)."""
        if self._selected_span is None:
            # The Replace button is already disabled without a selection
            # (see _sync_replace_button); this guard is purely defensive
            # in case _on_replace is ever invoked directly.
            return
        replacement = self._replacement_edit.text()
        temp_out = self._next_temp_path()
        try:
            PDFService().replace_text(
                self._working_path, temp_out, self._page, self._selected_span, replacement
            )
        except (EntradaInvalidaError, PDFCorruptoError) as exc:
            self._feedback.setText(error_message(exc))
            return  # NO advance, selection preserved for retry
        if not self._render(temp_out, terminal_on_error=False):
            return  # post-write render failed: NO advance, old view intact
        self._working_path = temp_out  # advance only after a fully visible edit
        self._clear_selection()  # old SpanInfo is stale after the page changed
        self._replacement_edit.clear()
        self._feedback.setText("Replacement applied.")

    def _on_save_as(self) -> None:
        """Always-prompt Save As.

        There is no separate document path to overwrite (the original is
        immutable), so Save and Save As collapse into one dialog that
        always asks for a destination. Copies the current working copy
        to the chosen destination; session state is left unchanged so
        the user may continue editing and save again. Cancel is a
        no-op."""
        default_name = suggest_output_name(self._original_path, "_edited", ".pdf")
        default_path = self._original_path.parent / default_name
        chosen, _selected_filter = QFileDialog.getSaveFileName(
            self, "Save As", str(default_path), "PDF files (*.pdf)"
        )
        # Qt's getSaveFileName returns a 2-tuple; on cancel both elements
        # are empty strings ('', ''), never None, so this falsy check is
        # sufficient.
        if not chosen:
            return
        shutil.copyfile(self._working_path, chosen)
        self._feedback.setText(f"Saved to {Path(chosen).name}")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override naming
        """Wipe this window's temp dir on close.

        Superseded edit copies (from earlier replacements) live only in
        `self._temp_dir` and are never referenced again once the window
        closes — there is no undo feature that would need them. Qt
        reliably calls `closeEvent` on `.close()`, so this is a safe,
        deterministic place to clean up rather than relying on
        process-exit or `__del__` timing."""
        shutil.rmtree(self._temp_dir, ignore_errors=True)
        super().closeEvent(event)

    def _fit(self) -> None:
        if self._pixmap_item is None:
            return
        vp = self._view.viewport().size()
        if vp.width() <= 0 or vp.height() <= 0:
            # A spurious resizeEvent fires during window construction,
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
