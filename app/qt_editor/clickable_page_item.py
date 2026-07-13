"""`ClickablePageItem` — a `QGraphicsPixmapItem` that forwards left-clicks.

Own module (one concern per module — `sdd/qt-advanced-editor-slice1/design`
D3 convention). See `sdd/qt-advanced-editor-slice2/design` D1: item-local
`event.pos()` for a pixmap anchored at scene origin (0,0) is already in
`PagePreviewResult`'s pixel space, so no extra `mapToScene`/un-fitting step
is needed. Empirically confirmed (E1) that `QGraphicsPixmapItem` accepts
`Qt.MouseButton.LeftButton` by default — `setAcceptedMouseButtons` below is
redundant but kept as explicit, self-documenting intent.
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
