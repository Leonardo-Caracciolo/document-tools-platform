"""Synthetic PDF/JPG builders for `PDFService` tests.

Every builder writes into a caller-supplied path (always `tmp_path`-backed
in tests) and returns that same path. No binaries are committed to the
repository — every fixture artifact is generated on the fly.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import img2pdf
import pikepdf
import pymupdf
from PIL import Image


def make_valid_pdf(path: Path, pages: int = 3) -> Path:
    """Write a valid, unencrypted PDF with `pages` blank pages to `path`."""
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(612, 792))
    pdf.save(path)
    return path


def make_native_text_pdf(path: Path, text: str) -> Path:
    """Write a single-page PDF with `text` inserted as real, selectable text to `path`.

    Confirmed during `sdd/pdf-to-word/design`'s empirical pass:
    `pymupdf`'s `page.insert_text` produces genuine extractable text
    (`page.get_text()` returns it, non-empty) and converts correctly
    end-to-end via `pdf2docx`. `make_valid_pdf` CANNOT be reused for this
    purpose — its pages are blank, so `get_text()` returns `''` and the
    scanned-PDF detection in `ExportService.pdf_a_word` would wrongly
    reject it.
    """
    doc = pymupdf.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), text)
        doc.save(path)
    finally:
        doc.close()
    return path


def make_table_pdf(path: Path, tables: list[list[list[str]]]) -> Path:
    """Write a PDF with one ruled-grid table per page to `path`.

    Each entry in `tables` becomes its own page: a ruled grid drawn via
    `page.draw_line()` (horizontal + vertical cell boundaries) with every
    cell's value placed via `page.insert_text()`. Confirmed during
    `sdd/pdf-to-excel/design`'s empirical pass: this construction is
    detected correctly by `pdfplumber.extract_tables()` under DEFAULT
    settings — no `table_settings` override needed. Row/column counts may
    vary per table; ragged rows (fewer cells than the widest row) are
    supported, matching `pdfplumber`'s own `Optional[str]` cell shape.
    """
    left, top = 72, 72
    cell_width, cell_height = 100, 24

    doc = pymupdf.open()
    try:
        for table in tables:
            n_rows = len(table)
            n_cols = max((len(row) for row in table), default=0)
            page = doc.new_page(width=612, height=792)

            for row_index in range(n_rows + 1):
                y = top + row_index * cell_height
                page.draw_line((left, y), (left + n_cols * cell_width, y))
            for col_index in range(n_cols + 1):
                x = left + col_index * cell_width
                page.draw_line((x, top), (x, top + n_rows * cell_height))

            for row_index, row in enumerate(table):
                for col_index, value in enumerate(row):
                    x = left + col_index * cell_width + 4
                    y = top + row_index * cell_height + cell_height - 8
                    page.insert_text((x, y), str(value))
        doc.save(path)
    finally:
        doc.close()
    return path


def make_encrypted_pdf(path: Path, owner: str = "o", user: str = "u", pages: int = 1) -> Path:
    """Write a PDF encrypted with `owner`/`user` passwords to `path`."""
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(612, 792))
    pdf.save(path, encryption=pikepdf.Encryption(owner=owner, user=user))
    return path


def make_corrupt_pdf(path: Path) -> Path:
    """Write a structurally malformed PDF (unparsable garbage) to `path`."""
    path.write_bytes(b"%PDF-1.7\n<<garbage>>")
    return path


def make_empty_file(path: Path) -> Path:
    """Write a 0-byte file to `path`."""
    path.write_bytes(b"")
    return path


def make_jpg(path: Path) -> Path:
    """Write a valid, minimal JPEG image to `path`."""
    Image.new("RGB", (64, 64), "white").save(path, "JPEG")
    return path


def make_image_heavy_pdf(path: Path) -> Path:
    """Write a single-page PDF embedding a large, high-quality raster image to `path`.

    The image is ~2000x2000 random-noise pixels saved at JPEG quality 95 —
    empirically confirmed (Sprint `compress-pdf` design/orchestrator
    verification) as the size/quality floor needed to guarantee
    `PDFService.compress`'s `rewrite_images(dpi_target=150, quality=75)`
    actually shrinks the result. Random noise (rather than a flat color)
    keeps the source JPEG large and not already near-maximally compressed.
    """
    pixels = os.urandom(2000 * 2000 * 3)
    image = Image.frombytes("RGB", (2000, 2000), pixels)

    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95)

    path.write_bytes(img2pdf.convert(buffer.getvalue()))
    return path


def make_text_pdf_with_matches(path: Path, pages_text: list[str]) -> Path:
    """Write a multi-page PDF to `path`, one page per `pages_text` entry.

    Each page gets its own `pages_text[i]` string inserted as real,
    selectable text near the top-left (72, 72) via `page.insert_text` —
    same pattern as `make_native_text_pdf`, extended to multiple pages
    with independently controllable content per page. This lets
    `highlight_text`/`redact_text` tests assert "matches on pages 1 and
    3 only, page 2 untouched" per the spec's page-scope scenarios. Blank
    pages (`""` entries) are supported for the "no match on this page"
    case.
    """
    doc = pymupdf.open()
    try:
        for text in pages_text:
            page = doc.new_page(width=612, height=792)
            if text:
                page.insert_text((72, 72), text)
        doc.save(path)
    finally:
        doc.close()
    return path


def make_corrupt_jpg(path: Path) -> Path:
    """Write a non-image file (plain bytes, `.jpg` extension) to `path`.

    Pillow's `Image.open()` raises `UnidentifiedImageError` for this —
    it never gets far enough to reach `Image.verify()`.
    """
    path.write_bytes(b"this is not an image, just plain garbage bytes")
    return path
