"""Synthetic `.docx` builder for `ExportService`/`ComWordProvider` tests.

Parallel to `pdf_factory.py` rather than folding into it: `.docx` and PDF
fixtures serve different test suites and have no shared build logic.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document


def make_valid_docx(path: Path) -> Path:
    """Write a minimal, valid `.docx` (single paragraph) to `path`.

    Empirically confirmed (design's Testing Strategy) that a minimal
    `python-docx`-generated `.docx` built this way is accepted by real
    Word COM automation and converts successfully.
    """
    document = Document()
    document.add_paragraph("Export service test document.")
    document.save(path)
    return path
