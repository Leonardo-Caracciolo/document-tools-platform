"""Shared pytest fixtures for synthetic PDF/JPG/`.docx` test artifacts.

Wraps `tests.fixtures.pdf_factory` and `tests.fixtures.docx_factory`
builders as `tmp_path`-scoped fixtures so `PDFService`/`ExportService`
tests never depend on committed binary files.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.fixtures.docx_factory import make_valid_docx
from tests.fixtures.image_pdf_factory import make_image_only_pdf
from tests.fixtures.pdf_factory import (
    make_corrupt_jpg,
    make_corrupt_pdf,
    make_empty_file,
    make_encrypted_pdf,
    make_image_heavy_pdf,
    make_jpg,
    make_native_text_pdf,
    make_valid_pdf,
)


@pytest.fixture
def valid_pdf_factory(tmp_path: Path) -> Callable[..., Path]:
    """Return a callable that writes a valid PDF under `tmp_path`."""

    def _make(name: str = "valid.pdf", pages: int = 3) -> Path:
        return make_valid_pdf(tmp_path / name, pages=pages)

    return _make


@pytest.fixture
def encrypted_pdf_factory(tmp_path: Path) -> Callable[..., Path]:
    """Return a callable that writes an encrypted PDF under `tmp_path`."""

    def _make(
        name: str = "encrypted.pdf", owner: str = "o", user: str = "u", pages: int = 1
    ) -> Path:
        return make_encrypted_pdf(tmp_path / name, owner=owner, user=user, pages=pages)

    return _make


@pytest.fixture
def corrupt_pdf_factory(tmp_path: Path) -> Callable[..., Path]:
    """Return a callable that writes a corrupt PDF under `tmp_path`."""

    def _make(name: str = "corrupt.pdf") -> Path:
        return make_corrupt_pdf(tmp_path / name)

    return _make


@pytest.fixture
def empty_file_factory(tmp_path: Path) -> Callable[..., Path]:
    """Return a callable that writes a 0-byte file under `tmp_path`."""

    def _make(name: str = "empty.pdf") -> Path:
        return make_empty_file(tmp_path / name)

    return _make


@pytest.fixture
def jpg_factory(tmp_path: Path) -> Callable[..., Path]:
    """Return a callable that writes a valid JPEG under `tmp_path`."""

    def _make(name: str = "image.jpg") -> Path:
        return make_jpg(tmp_path / name)

    return _make


@pytest.fixture
def corrupt_jpg_factory(tmp_path: Path) -> Callable[..., Path]:
    """Return a callable that writes a non-image `.jpg` file under `tmp_path`."""

    def _make(name: str = "corrupt.jpg") -> Path:
        return make_corrupt_jpg(tmp_path / name)

    return _make


@pytest.fixture
def image_heavy_pdf_factory(tmp_path: Path) -> Callable[..., Path]:
    """Return a callable that writes an image-heavy PDF under `tmp_path`."""

    def _make(name: str = "image_heavy.pdf") -> Path:
        return make_image_heavy_pdf(tmp_path / name)

    return _make


@pytest.fixture
def valid_docx_factory(tmp_path: Path) -> Callable[..., Path]:
    """Return a callable that writes a valid `.docx` under `tmp_path`."""

    def _make(name: str = "valid.docx") -> Path:
        return make_valid_docx(tmp_path / name)

    return _make


@pytest.fixture
def image_only_pdf_factory(tmp_path: Path) -> Callable[..., Path]:
    """Return a callable that writes an image-only PDF (no selectable text) under `tmp_path`."""

    def _make(name: str = "image_only.pdf", text: str = "Factura Total") -> Path:
        return make_image_only_pdf(tmp_path / name, text)

    return _make


@pytest.fixture
def native_text_pdf_factory(tmp_path: Path) -> Callable[..., Path]:
    """Return a callable that writes a PDF with real, extractable text under `tmp_path`."""

    def _make(
        name: str = "native_text.pdf", text: str = "Documento con texto nativo de prueba."
    ) -> Path:
        return make_native_text_pdf(tmp_path / name, text)

    return _make
