"""Synthetic image-only PDF builder for `OCRService` tests.

Parallel to `pdf_factory.py`/`docx_factory.py` rather than folding into
either — this fixture serves `OCRService`'s fake-provider-driven test
suite specifically, and has no shared build logic with the other two.

Confirmed during design (`sdd/ocr-pdf-provider/design`, "Empirical status"
(d)): a PDF built by rendering text onto a raster image via Pillow and
embedding it via `pymupdf`'s `page.insert_image` has zero pre-existing
selectable text (`page.get_text()` returns `''`) — the OCR text layer
`OCRService` adds is the only text `output` will ever contain.
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

#: PDF points per inch, and the source raster's assumed DPI — together
#: these size the page in points so the embedded image fills it exactly
#: at that DPI. Matches the DPI `OCRService.ocr` rasterizes pages at.
_POINTS_PER_INCH = 72
_SOURCE_DPI = 300

#: Best-effort TrueType fonts to render legible glyphs with. Falls back to
#: Pillow's tiny built-in bitmap font if neither is present — harmless
#: here because `OCRService`'s unit tests are fake-provider-driven (per
#: spec's "Provider-Agnostic Testability" requirement) and never run real
#: recognition against the rendered glyphs, unlike
#: `tests/integration/test_tesseract_ocr_provider.py`'s own font loader,
#: which skips instead of falling back for that reason.
_CANDIDATE_FONTS = (
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
)


def _load_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for candidate in _CANDIDATE_FONTS:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def make_image_only_pdf(
    path: Path, text: str, size: tuple[int, int] = (2550, 3300)
) -> Path:
    """Write a 1-page, image-only PDF with `text` rendered onto it to `path`.

    `size` is the source raster's pixel dimensions at `_SOURCE_DPI`
    (default: 2550x3300, an 8.5x11in page at 300 DPI). The PDF page is
    sized in points (`size * 72 / _SOURCE_DPI`) so the embedded image
    fills the full page — the same DPI `OCRService.ocr` rasterizes pages
    back at, keeping any pixel-space OCR coordinates directly comparable
    to this fixture's input.
    """
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).text((150, 150), text, fill="black", font=_load_font(80))

    buffer = io.BytesIO()
    image.save(buffer, "PNG")

    width_pt = size[0] * _POINTS_PER_INCH / _SOURCE_DPI
    height_pt = size[1] * _POINTS_PER_INCH / _SOURCE_DPI

    doc = pymupdf.open()
    try:
        page = doc.new_page(width=width_pt, height=height_pt)
        page.insert_image(page.rect, stream=buffer.getvalue())
        doc.save(path)
    finally:
        doc.close()
    return path
