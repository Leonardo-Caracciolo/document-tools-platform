"""Tests for `app.core.services.pdf_service`.

Covers the PR1 foundation (constructor, `_translate_errors` mapping,
`_validate_pages` helper) plus the PR2 `merge`/`split` operations.
`organize`/`protect`/`unlock`/`jpg_to_pdf` are implemented in later
Sprint 1 PRs and have no tests here yet.
"""

from __future__ import annotations

from collections.abc import Callable
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


class TestMerge:
    """Tests for `PDFService.merge`."""

    def test_merge_two_valid_pdfs_preserves_page_order(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        first = valid_pdf_factory("first.pdf", pages=2)
        second = valid_pdf_factory("second.pdf", pages=3)
        service = PDFService()
        output = tmp_path / "merged.pdf"

        result = service.merge([first, second], output)

        assert result == output
        with pikepdf.Pdf.open(output) as merged:
            assert len(merged.pages) == 5

    def test_merge_single_file_is_a_content_passthrough(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("only.pdf", pages=2)
        service = PDFService()
        output = tmp_path / "merged.pdf"

        service.merge([source], output)

        with pikepdf.Pdf.open(output) as merged:
            assert len(merged.pages) == 2

    def test_merge_raises_entrada_invalida_for_zero_files(self, tmp_path: Path) -> None:
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.merge([], tmp_path / "merged.pdf")

    def test_merge_raises_entrada_invalida_for_empty_input_file(
        self,
        tmp_path: Path,
        valid_pdf_factory: Callable[..., Path],
        empty_file_factory: Callable[..., Path],
    ) -> None:
        good = valid_pdf_factory("good.pdf")
        empty = empty_file_factory("empty.pdf")
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.merge([good, empty], tmp_path / "merged.pdf")

    def test_merge_raises_pdf_corrupto_naming_offending_file(
        self,
        tmp_path: Path,
        valid_pdf_factory: Callable[..., Path],
        corrupt_pdf_factory: Callable[..., Path],
    ) -> None:
        good = valid_pdf_factory("good.pdf")
        bad = corrupt_pdf_factory("bad.pdf")
        service = PDFService()

        with pytest.raises(PDFCorruptoError, match="bad.pdf"):
            service.merge([good, bad], tmp_path / "merged.pdf")


class TestSplit:
    """Tests for `PDFService.split`."""

    def test_split_creates_one_file_per_range(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=5)
        service = PDFService()

        outputs = service.split(source, tmp_path / "out", ranges=[(1, 2), (3, 5)])

        assert len(outputs) == 2
        with pikepdf.Pdf.open(outputs[0]) as first:
            assert len(first.pages) == 2
        with pikepdf.Pdf.open(outputs[1]) as second:
            assert len(second.pages) == 3

    def test_split_defaults_to_one_file_per_page(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=3)
        service = PDFService()

        outputs = service.split(source, tmp_path / "out")

        assert len(outputs) == 3
        for output in outputs:
            with pikepdf.Pdf.open(output) as chunk:
                assert len(chunk.pages) == 1

    def test_split_raises_entrada_invalida_for_out_of_range_page(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=3)
        service = PDFService()

        with pytest.raises(EntradaInvalidaError, match="5"):
            service.split(source, tmp_path / "out", ranges=[(1, 5)])

    def test_split_single_page_pdf_produces_one_output_equal_to_source(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=1)
        service = PDFService()

        outputs = service.split(source, tmp_path / "out", ranges=[(1, 1)])

        assert len(outputs) == 1
        with pikepdf.Pdf.open(outputs[0]) as chunk:
            assert len(chunk.pages) == 1

    def test_split_raises_entrada_invalida_for_zero_page_pdf(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("empty.pdf", pages=0)
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.split(source, tmp_path / "out")

    def test_split_raises_entrada_invalida_for_empty_source_file(
        self, tmp_path: Path, empty_file_factory: Callable[..., Path]
    ) -> None:
        source = empty_file_factory("empty.pdf")
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.split(source, tmp_path / "out")
