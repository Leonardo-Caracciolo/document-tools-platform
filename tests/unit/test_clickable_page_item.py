"""Tests for `ClickablePageItem` — `sdd/qt-advanced-editor-slice2/design`
D1 (E1-confirmed click delivery).

Skips cleanly via `importorskip` when PySide6 is absent from the active
env (it is an optional dependency group — see `pyproject.toml`). Runs
under `QT_QPA_PLATFORM=offscreen` so no window is actually displayed.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402 - must follow importorskip/env setup
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
)

from app.qt_editor.clickable_page_item import ClickablePageItem  # noqa: E402


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


def _make_mouse_event(pos: QPointF, button: Qt.MouseButton) -> QGraphicsSceneMouseEvent:
    ev = QGraphicsSceneMouseEvent(QEvent.Type.GraphicsSceneMousePress)
    ev.setPos(pos)
    ev.setButton(button)
    return ev


class TestClickDelivery:
    def test_send_event_forwards_item_local_pos_to_callback(self, qapp: QApplication) -> None:
        scene = QGraphicsScene()
        received: list[QPointF] = []
        item = ClickablePageItem(QPixmap(400, 300), on_click=received.append)
        scene.addItem(item)
        ev = _make_mouse_event(QPointF(150.0, 80.0), Qt.MouseButton.LeftButton)

        scene.sendEvent(item, ev)

        assert received == [QPointF(150.0, 80.0)]

    def test_direct_mouse_press_event_forwards_item_local_pos_to_callback(
        self, qapp: QApplication
    ) -> None:
        received: list[QPointF] = []
        item = ClickablePageItem(QPixmap(400, 300), on_click=received.append)
        ev = _make_mouse_event(QPointF(200.0, 100.0), Qt.MouseButton.LeftButton)

        item.mousePressEvent(ev)

        assert received == [QPointF(200.0, 100.0)]

    def test_non_left_button_does_not_invoke_callback(self, qapp: QApplication) -> None:
        received: list[QPointF] = []
        item = ClickablePageItem(QPixmap(400, 300), on_click=received.append)
        ev = _make_mouse_event(QPointF(10.0, 10.0), Qt.MouseButton.RightButton)

        item.mousePressEvent(ev)

        assert received == []
