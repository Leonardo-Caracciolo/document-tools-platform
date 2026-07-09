"""Tests for `app.core.services.pdf_service` (PR1 foundation only).

Operation methods (merge/split/organize/protect/unlock/jpg_to_pdf) are
implemented in later Sprint 1 PRs and have no tests here yet. This
module covers only the constructor, `_translate_errors` mapping, and
`_validate_pages` helper scaffolded in PR1.
"""

from __future__ import annotations

from pathlib import Path

import img2pdf
import pikepdf
import pytest
from PIL import UnidentifiedImageError

from app.core.exceptions import (
    ArchivoProtegidoError,
    ContrasenaInvalidaError,
    EntradaInvalidaError,
    PDFCorruptoError,
)
from app.core.services.pdf_service import PDFService
from tests.fixtures.pdf_factory import make_corrupt_pdf, make_empty_file, make_valid_pdf


def test_can_construct_service() -> None:
    service = PDFService()

    assert service._log.name == "app.core.services.pdf_service"


def test_require_nonempty_file_accepts_valid_pdf(tmp_path: Path) -> None:
    service = PDFService()
    path = make_valid_pdf(tmp_path / "valid.pdf")

    service._require_nonempty_file(path)


def test_require_nonempty_file_rejects_empty_file(tmp_path: Path) -> None:
    service = PDFService()
    path = make_empty_file(tmp_path / "empty.pdf")

    with pytest.raises(EntradaInvalidaError):
        service._require_nonempty_file(path)


def test_require_nonempty_file_rejects_missing_file(tmp_path: Path) -> None:
    service = PDFService()

    with pytest.raises(EntradaInvalidaError):
        service._require_nonempty_file(tmp_path / "does-not-exist.pdf")


def test_empty_file_and_corrupt_file_are_distinguishable_before_opening(
    tmp_path: Path,
) -> None:
    """Regression test: `pikepdf` itself raises the identical `PdfError`
    for a 0-byte file and for garbage bytes, so the empty/corrupt
    distinction only exists if `_require_nonempty_file` runs first."""
    service = PDFService()
    empty_path = make_empty_file(tmp_path / "empty.pdf")
    corrupt_path = make_corrupt_pdf(tmp_path / "corrupt.pdf")

    with pytest.raises(EntradaInvalidaError):
        service._require_nonempty_file(empty_path)

    # A non-empty-but-corrupt file must NOT be rejected by this helper —
    # that's `_translate_errors`' job (PDFCorruptoError), not this one's.
    service._require_nonempty_file(corrupt_path)


def test_validate_pages_accepts_in_range_pages() -> None:
    service = PDFService()

    service._validate_pages([1, 2, 3], page_count=3, source=Path("doc.pdf"))


def test_validate_pages_rejects_empty_pages() -> None:
    service = PDFService()

    with pytest.raises(EntradaInvalidaError):
        service._validate_pages([], page_count=3, source=Path("doc.pdf"))


def test_validate_pages_rejects_out_of_range_page() -> None:
    service = PDFService()

    with pytest.raises(EntradaInvalidaError):
        service._validate_pages([1, 4], page_count=3, source=Path("doc.pdf"))


def test_translate_errors_maps_pdf_error_to_pdf_corrupto() -> None:
    service = PDFService()

    with (
        pytest.raises(PDFCorruptoError),
        service._translate_errors("merge", Path("corrupt.pdf")),
    ):
        raise pikepdf.PdfError("bad xref")


def test_translate_errors_maps_password_error_on_unlock_to_contrasena_invalida() -> None:
    service = PDFService()

    with (
        pytest.raises(ContrasenaInvalidaError),
        service._translate_errors("unlock", Path("locked.pdf")),
    ):
        raise pikepdf.PasswordError("wrong password")


def test_translate_errors_maps_password_error_on_other_op_to_archivo_protegido() -> None:
    service = PDFService()

    with (
        pytest.raises(ArchivoProtegidoError),
        service._translate_errors("protect", Path("locked.pdf")),
    ):
        raise pikepdf.PasswordError("password required")


def test_translate_errors_maps_unidentified_image_to_entrada_invalida() -> None:
    service = PDFService()

    with (
        pytest.raises(EntradaInvalidaError),
        service._translate_errors("jpg_to_pdf", Path("notanimage.jpg")),
    ):
        raise UnidentifiedImageError("cannot identify image file")


def test_translate_errors_maps_img2pdf_image_open_error_to_entrada_invalida() -> None:
    service = PDFService()

    with (
        pytest.raises(EntradaInvalidaError),
        service._translate_errors("jpg_to_pdf", Path("broken.jpg")),
    ):
        raise img2pdf.ImageOpenError("cannot open image")
