"""`ClickablePageItem` — a `QGraphicsPixmapItem` that forwards left-clicks.

Its own module because hit-testing is a distinct, reusable concern kept
separate from the window that owns it — matching this package's
one-class-per-module convention (`editor_window.py` owns everything
else).

The click callback receives `event.pos()`, which Qt reports in
ITEM-LOCAL coordinates. Because this pixmap is always added at scene
origin (0, 0) with no additional transform, item-local pixels are
already exactly the pixel space `PagePreviewResult` (and therefore
`AdvancedEditorWindow._on_canvas_click`'s zoom/origin math) expects — no
extra `mapToScene()`/un-fitting step is needed here or in the caller.

`setAcceptedMouseButtons(Qt.MouseButton.LeftButton)` below is redundant
in current PySide6 (a `QGraphicsPixmapItem` accepts left-clicks by
default — confirmed empirically against real PySide6 6.11.1 runs), but
is kept as explicit, self-documenting intent in case that default ever
changes.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsSceneMouseEvent


class ClickablePageItem(QGraphicsPixmapItem):
    """Pixmap item that forwards left-clicks (item-local pixel coords) to a callback."""

    def __init__(self, pixmap: QPixmap, on_click: Callable[[QPointF], None]) -> None:
        super().__init__(pixmap)
        self._on_click = on_click
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802 - Qt override naming
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click(event.pos())
        super().mousePressEvent(event)
