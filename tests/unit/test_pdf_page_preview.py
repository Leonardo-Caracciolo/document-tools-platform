"""Tests for `PdfPagePreview` — `sdd/edit-pdf-preview/design` "Widget
structure" + spec "Click-to-Point Capture"/"Reliable Click Registration on
Rendered Preview"/"Preview Rendering Graceful Degradation".

First test module for this widget. Uses the shared session-scoped
`tk_root` fixture from `tests/conftest.py` (a real live Tk root) — never
define a local `ctk.CTk()` root, per this project's established
convention (a real `_tkinter.TclError` was hit earlier in this project's
history from exactly that mistake).

Per the design's HARD CONSTRAINT, click simulation MUST target
`widget._label._label` (the internal real `tkinter.Label`) — NEVER
`widget._label` (the `CTkLabel` wrapper), which silently never fires.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import customtkinter as ctk
from PIL import Image

from app.core.services.pdf_service import PagePreviewResult
from app.ui.widgets.pdf_page_preview import PdfPagePreview


def _make_result(zoom: float = 0.5, origin: tuple[float, float] = (0.0, 0.0)) -> PagePreviewResult:
    image = Image.new("RGB", (100, 140), color="white")
    return PagePreviewResult(image=image, zoom=zoom, origin=origin)


def _click(preview: PdfPagePreview, x: int, y: int) -> None:
    """Simulate a `<Button-1>` click on `preview`'s inner real
    `tkinter.Label` (`preview._label._label` — see the design's HARD
    CONSTRAINT), temporarily making the shared session-scoped `tk_root`
    viewable if needed.

    **Empirically discovered** (this session, real customtkinter 6.0.0 /
    Tk on Windows): `event_generate` for a physical pointer event like
    `<Button-1>` only actually invokes bound scripts when the target
    widget is VIEWABLE (its whole ancestor chain up to a mapped
    toplevel) — `tests/conftest.py`'s `tk_root` fixture is `withdraw()`n
    for the whole test session (deliberately, to avoid flashing a
    window), so a click generated against it with no accommodation
    silently does nothing (no error, no callback — the exact kind of
    silent failure this widget's own click binding has to guard
    against). Grids `preview` onto its master if not already mapped,
    deiconifies the toplevel just long enough to deliver the synthetic
    event, then restores the toplevel's prior withdrawn state so later
    test modules relying on a hidden root are unaffected.
    """
    if not preview.winfo_ismapped():
        preview.grid()
    toplevel = preview.winfo_toplevel()
    was_withdrawn = toplevel.state() == "withdrawn"
    if was_withdrawn:
        toplevel.deiconify()
        toplevel.update()
    preview._label._label.event_generate("<Button-1>", x=x, y=y)
    if was_withdrawn:
        toplevel.withdraw()


class TestShow:
    def test_show_renders_image_and_records_zoom_origin(self, tk_root: ctk.CTk) -> None:
        widget = PdfPagePreview(tk_root, on_point=MagicMock())
        result = _make_result(zoom=0.42, origin=(1.0, 2.0))

        widget.show(result)

        assert widget._ctk_image is not None
        assert widget._zoom == 0.42
        assert widget._origin == (1.0, 2.0)
        assert widget._label.cget("image") is widget._ctk_image

    def test_show_placeholder_clears_image_and_zoom(self, tk_root: ctk.CTk) -> None:
        widget = PdfPagePreview(tk_root, on_point=MagicMock())
        widget.show(_make_result())

        widget.show_placeholder("No preview")

        assert widget._ctk_image is None
        assert widget._zoom == 0.0
        assert widget._label.cget("text") == "No preview"

    def test_show_after_placeholder_still_renders(self, tk_root: ctk.CTk) -> None:
        # Regression guard for the design's V3 finding: the click binding
        # (and rendering itself) must survive an image -> None -> image swap.
        widget = PdfPagePreview(tk_root, on_point=MagicMock())
        widget.show(_make_result())
        widget.show_placeholder()

        widget.show(_make_result(zoom=0.75, origin=(3.0, 4.0)))

        assert widget._ctk_image is not None
        assert widget._zoom == 0.75
        assert widget._origin == (3.0, 4.0)


class TestClick:
    def test_click_after_show_computes_point_and_calls_on_point(self, tk_root: ctk.CTk) -> None:
        on_point = MagicMock()
        widget = PdfPagePreview(tk_root, on_point=on_point)
        widget.show(_make_result(zoom=0.5, origin=(10.0, 20.0)))

        _click(widget, x=40, y=60)

        on_point.assert_called_once()
        (point,) = on_point.call_args.args
        assert point == (40 / 0.5 + 10.0, 60 / 0.5 + 20.0)

    def test_click_binding_survives_image_swap(self, tk_root: ctk.CTk) -> None:
        # `_label._label.bind` happens once at construction (design's V3) —
        # confirm a click still fires the handler after a second `show()`.
        on_point = MagicMock()
        widget = PdfPagePreview(tk_root, on_point=on_point)
        widget.show(_make_result(zoom=0.5, origin=(0.0, 0.0)))
        widget.show(_make_result(zoom=0.25, origin=(0.0, 0.0)))

        _click(widget, x=8, y=8)

        on_point.assert_called_once()
        (point,) = on_point.call_args.args
        assert point == (8 / 0.25, 8 / 0.25)

    def test_click_after_placeholder_does_not_call_on_point(self, tk_root: ctk.CTk) -> None:
        on_point = MagicMock()
        widget = PdfPagePreview(tk_root, on_point=on_point)
        widget.show(_make_result())
        widget.show_placeholder()

        _click(widget, x=10, y=10)

        on_point.assert_not_called()

    def test_click_before_any_show_does_not_call_on_point(self, tk_root: ctk.CTk) -> None:
        on_point = MagicMock()
        widget = PdfPagePreview(tk_root, on_point=on_point)

        _click(widget, x=10, y=10)

        on_point.assert_not_called()
