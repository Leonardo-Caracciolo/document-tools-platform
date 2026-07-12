"""Single-page inline PDF preview widget — `sdd/edit-pdf-preview/design`
"Widget structure — `PdfPagePreview(ctk.CTkFrame)`".

Owns display (rendering a `PagePreviewResult` as a `CTkImage`) and the
pixel-to-point click math. Imports NO `pymupdf` — `PDFService.render_page`
stays the only place that library is touched (design's "pymupdf stays
behind the `PDFService` boundary").

**HARD CONSTRAINT (confirmed empirically against real customtkinter
6.0.0, design's EMPIRICAL VERIFICATION RESULTS V3)**: the click MUST be
bound on `self._label._label` — the internal real `tkinter.Label` — NOT
on the `CTkLabel` wrapper itself. Binding the wrapper is a silent no-op:
it raises no error and fires nothing. This is specific to `CTkLabel`; do
not generalize it to other customtkinter widgets (`CTkEntry` does not
have the same requirement, see the design's V2).
"""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from app.core.services.pdf_service import PagePreviewResult

_DEFAULT_PLACEHOLDER_MESSAGE = "No preview"


class PdfPagePreview(ctk.CTkFrame):
    """Renders one `PagePreviewResult` and reports clicks as PDF-space points.

    `on_point` is called with `(x, y)` in PDF point-space every time the
    user clicks the rendered image. Clicking before any `show()` call, or
    after `show_placeholder()`, is a guarded no-op — there is nothing to
    map a pixel back to.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_point: Callable[[tuple[float, float]], None],
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._on_point = on_point
        self._zoom: float = 0.0
        self._origin: tuple[float, float] = (0.0, 0.0)
        self._ctk_image: ctk.CTkImage | None = None

        self._label = ctk.CTkLabel(self, text="", image=None)
        self._label.grid(row=0, column=0, sticky="nsew")
        # Bind ONCE at construction, on the inner real tkinter.Label — see
        # module docstring's HARD CONSTRAINT. Confirmed to survive every
        # later `configure(image=...)` swap (design's V3).
        self._label._label.bind("<Button-1>", self._on_click)

    def show(self, result: PagePreviewResult) -> None:
        """Render `result` and record the zoom/origin needed for click mapping."""
        self._ctk_image = ctk.CTkImage(
            light_image=result.image,
            size=(result.image.width, result.image.height),
        )
        self._label.configure(image=self._ctk_image, text="")
        self._zoom = result.zoom
        self._origin = result.origin

    def show_placeholder(self, message: str = _DEFAULT_PLACEHOLDER_MESSAGE) -> None:
        """Clear the image and show `message` instead.

        Resets `_ctk_image`/`_zoom` so a click after a placeholder (or
        before any `show()` call) is impossible to misinterpret as a real
        point — `_on_click`'s guard checks exactly these two attributes.

        **Empirically discovered gotcha (this session, real customtkinter
        6.0.0)**: `CTkLabel.configure(image=None)` does NOT clear the
        underlying real `tkinter.Label`'s native `-image` option —
        `CTkLabel._update_image()` only calls `self._label.configure(image=...)`
        when the new image is a `CTkImage` or any other non-`None` value;
        for `None` it does nothing, leaving the native label still
        pointing at the OLD image's now-about-to-be-GC'd Tcl photo. Once
        `self._ctk_image` is dropped below and garbage collected, that
        stale reference becomes a dangling Tcl image name, and the next
        *any* `configure()` call on the label — even an unrelated
        `text=`-only one — raises `_tkinter.TclError: image "..." doesn't
        exist`. Clearing the native label's image directly first (`""` is
        the standard empty-image sentinel) sidesteps this entirely and
        keeps `show()` safely callable again afterward.
        """
        self._label._label.configure(image="")
        self._label.configure(image=None, text=message)
        self._ctk_image = None
        self._zoom = 0.0

    def _on_click(self, event: object) -> None:
        """`<Button-1>` handler bound on `self._label._label`.

        No-ops when there is no rendered image (`_ctk_image is None`) or
        `_zoom` is non-positive (set by `show_placeholder`, or never set
        by a `show()` call yet) — either way there is no valid zoom to
        divide by.
        """
        if self._ctk_image is None or self._zoom <= 0:
            return
        point = (
            event.x / self._zoom + self._origin[0],
            event.y / self._zoom + self._origin[1],
        )
        self._on_point(point)
