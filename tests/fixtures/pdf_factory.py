"""Synthetic PDF/JPG builders for `PDFService` tests.

Every builder writes into a caller-supplied path (always `tmp_path`-backed
in tests) and returns that same path. No binaries are committed to the
repository — every fixture artifact is generated on the fly.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
from PIL import Image


def make_valid_pdf(path: Path, pages: int = 3) -> Path:
    """Write a valid, unencrypted PDF with `pages` blank pages to `path`."""
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(612, 792))
    pdf.save(path)
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
