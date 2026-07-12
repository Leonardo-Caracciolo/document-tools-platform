"""Tests for `app.core.services.pdf_service`.

Covers the PR1 foundation (constructor, `_translate_errors` mapping,
`_validate_pages` helper), the PR2 `merge`/`split` operations, the PR3
`organize`/`protect`/`unlock` operations, the PR4 `jpg_to_pdf`
operation, the `compress` operation, the `edit-pdf` PR1 `add_text`/
`highlight_text`/`redact_text` operations, plus cross-cutting logging/
exception-containment tests exercised across all ten operations.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import img2pdf
import pikepdf
import pymupdf
import pytest
from PIL import UnidentifiedImageError

from app.core.exceptions import (
    ArchivoProtegidoError,
    ContrasenaInvalidaError,
    EntradaInvalidaError,
    PDFCorruptoError,
    PDFSinCoincidenciasError,
)
from app.core.services.pdf_service import PagePreviewResult, PDFService
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

    def test_split_rejects_start_after_end_without_orphaning_earlier_chunks(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=5)
        service = PDFService()
        out_dir = tmp_path / "out"

        with pytest.raises(EntradaInvalidaError, match=r"\(5, 3\)"):
            service.split(source, out_dir, ranges=[(1, 2), (5, 3)])

        assert not out_dir.exists()


class TestOrganize:
    """Tests for `PDFService.organize`."""

    def test_organize_reorders_pages_per_spec(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=3)
        service = PDFService()
        output = tmp_path / "organized.pdf"

        result = service.organize(source, output, order=[3, 1, 2])

        assert result == output
        with pikepdf.Pdf.open(output) as organized:
            assert len(organized.pages) == 3

    def test_organize_can_drop_pages_not_referenced_in_order(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=3)
        service = PDFService()
        output = tmp_path / "organized.pdf"

        service.organize(source, output, order=[2])

        with pikepdf.Pdf.open(output) as organized:
            assert len(organized.pages) == 1

    def test_organize_raises_entrada_invalida_for_out_of_range_index(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=3)
        service = PDFService()

        with pytest.raises(EntradaInvalidaError, match="5"):
            service.organize(source, tmp_path / "organized.pdf", order=[1, 5])

    def test_organize_raises_entrada_invalida_for_duplicate_index(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=3)
        service = PDFService()

        with pytest.raises(EntradaInvalidaError, match="Duplicate"):
            service.organize(source, tmp_path / "organized.pdf", order=[1, 1, 2])

    def test_organize_raises_entrada_invalida_for_empty_order(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=3)
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.organize(source, tmp_path / "organized.pdf", order=[])

    def test_organize_raises_entrada_invalida_for_empty_source_file(
        self, tmp_path: Path, empty_file_factory: Callable[..., Path]
    ) -> None:
        source = empty_file_factory("empty.pdf")
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.organize(source, tmp_path / "organized.pdf", order=[1])

    def test_organize_rejects_out_of_range_index_without_orphaning_output_dir(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=3)
        service = PDFService()
        output = tmp_path / "out" / "organized.pdf"

        with pytest.raises(EntradaInvalidaError):
            service.organize(source, output, order=[1, 4])

        assert not output.parent.exists()


class TestProtect:
    """Tests for `PDFService.protect`."""

    def test_protect_produces_aes256_encrypted_pdf_requiring_password(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=2)
        service = PDFService()
        output = tmp_path / "protected.pdf"

        result = service.protect(source, output, owner_password="secret")

        assert result == output
        with pytest.raises(pikepdf.PasswordError):
            pikepdf.Pdf.open(output)
        with pikepdf.Pdf.open(output, password="secret") as opened:
            assert opened.is_encrypted
            assert opened.encryption.R == 6
            assert opened.encryption.bits == 256

    def test_protect_supports_distinct_owner_and_user_passwords(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=1)
        service = PDFService()
        output = tmp_path / "protected.pdf"

        service.protect(source, output, owner_password="owner-pwd", user_password="user-pwd")

        with pikepdf.Pdf.open(output, password="user-pwd") as opened:
            assert opened.is_encrypted

    def test_protect_raises_archivo_protegido_for_already_encrypted_input(
        self, tmp_path: Path, encrypted_pdf_factory: Callable[..., Path]
    ) -> None:
        source = encrypted_pdf_factory("locked.pdf", owner="o", user="u")
        service = PDFService()

        with pytest.raises(ArchivoProtegidoError):
            service.protect(source, tmp_path / "protected.pdf", owner_password="secret")

    def test_protect_raises_archivo_protegido_for_owner_only_encrypted_input(
        self, tmp_path: Path, encrypted_pdf_factory: Callable[..., Path]
    ) -> None:
        """Regression: owner-only encryption (blank user password) opens
        with NO password at all, so `pikepdf.Pdf.open` never raises — the
        `pdf.is_encrypted` guard is the only thing catching this case."""
        source = encrypted_pdf_factory("locked.pdf", owner="owner-secret", user="")
        service = PDFService()

        with pytest.raises(ArchivoProtegidoError):
            service.protect(source, tmp_path / "protected.pdf", owner_password="secret")

    def test_protect_rejects_already_encrypted_input_without_orphaning_output_dir(
        self, tmp_path: Path, encrypted_pdf_factory: Callable[..., Path]
    ) -> None:
        source = encrypted_pdf_factory("locked.pdf", owner="o", user="u")
        service = PDFService()
        output = tmp_path / "out" / "protected.pdf"

        with pytest.raises(ArchivoProtegidoError):
            service.protect(source, output, owner_password="secret")

        assert not output.parent.exists()

    def test_protect_raises_entrada_invalida_for_empty_password(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf")
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.protect(source, tmp_path / "protected.pdf", owner_password="")

    def test_protect_raises_entrada_invalida_for_blank_password(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf")
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.protect(source, tmp_path / "protected.pdf", owner_password="   ")

    def test_protect_raises_entrada_invalida_for_empty_source_file(
        self, tmp_path: Path, empty_file_factory: Callable[..., Path]
    ) -> None:
        source = empty_file_factory("empty.pdf")
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.protect(source, tmp_path / "protected.pdf", owner_password="secret")


class TestUnlock:
    """Tests for `PDFService.unlock`."""

    def test_unlock_removes_password_protection(
        self, tmp_path: Path, encrypted_pdf_factory: Callable[..., Path]
    ) -> None:
        source = encrypted_pdf_factory("locked.pdf", owner="owner-pwd", user="user-pwd")
        service = PDFService()
        output = tmp_path / "unlocked.pdf"

        result = service.unlock(source, output, password="user-pwd")

        assert result == output
        with pikepdf.Pdf.open(output) as opened:
            assert not opened.is_encrypted

    def test_unlock_accepts_owner_password(
        self, tmp_path: Path, encrypted_pdf_factory: Callable[..., Path]
    ) -> None:
        source = encrypted_pdf_factory("locked.pdf", owner="owner-pwd", user="user-pwd")
        service = PDFService()
        output = tmp_path / "unlocked.pdf"

        service.unlock(source, output, password="owner-pwd")

        with pikepdf.Pdf.open(output) as opened:
            assert not opened.is_encrypted

    def test_unlock_raises_contrasena_invalida_for_wrong_password(
        self, tmp_path: Path, encrypted_pdf_factory: Callable[..., Path]
    ) -> None:
        source = encrypted_pdf_factory("locked.pdf", owner="owner-pwd", user="user-pwd")
        service = PDFService()

        with pytest.raises(ContrasenaInvalidaError):
            service.unlock(source, tmp_path / "unlocked.pdf", password="wrong")

    def test_unlock_raises_entrada_invalida_for_non_encrypted_input(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf")
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.unlock(source, tmp_path / "unlocked.pdf", password="whatever")

    def test_unlock_raises_entrada_invalida_for_empty_source_file(
        self, tmp_path: Path, empty_file_factory: Callable[..., Path]
    ) -> None:
        source = empty_file_factory("empty.pdf")
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.unlock(source, tmp_path / "unlocked.pdf", password="whatever")

    def test_unlock_rejects_non_encrypted_input_without_orphaning_output_dir(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf")
        service = PDFService()
        output = tmp_path / "out" / "unlocked.pdf"

        with pytest.raises(EntradaInvalidaError):
            service.unlock(source, output, password="whatever")

        assert not output.parent.exists()


class TestJpgToPdf:
    """Tests for `PDFService.jpg_to_pdf`."""

    def test_jpg_to_pdf_converts_multiple_images_to_one_pdf_in_order(
        self, tmp_path: Path, jpg_factory: Callable[..., Path]
    ) -> None:
        first = jpg_factory("first.jpg")
        second = jpg_factory("second.jpg")
        third = jpg_factory("third.jpg")
        service = PDFService()
        output = tmp_path / "images.pdf"

        result = service.jpg_to_pdf([first, second, third], output)

        assert result == output
        with pikepdf.Pdf.open(output) as pdf:
            assert len(pdf.pages) == 3

    def test_jpg_to_pdf_single_image_produces_one_page_pdf(
        self, tmp_path: Path, jpg_factory: Callable[..., Path]
    ) -> None:
        image = jpg_factory("only.jpg")
        service = PDFService()
        output = tmp_path / "images.pdf"

        service.jpg_to_pdf([image], output)

        with pikepdf.Pdf.open(output) as pdf:
            assert len(pdf.pages) == 1

    def test_jpg_to_pdf_raises_entrada_invalida_for_zero_images(self, tmp_path: Path) -> None:
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.jpg_to_pdf([], tmp_path / "images.pdf")

    def test_jpg_to_pdf_raises_entrada_invalida_naming_offending_file(
        self,
        tmp_path: Path,
        jpg_factory: Callable[..., Path],
        corrupt_jpg_factory: Callable[..., Path],
    ) -> None:
        good = jpg_factory("good.jpg")
        bad = corrupt_jpg_factory("bad.jpg")
        service = PDFService()

        with pytest.raises(EntradaInvalidaError, match="bad.jpg"):
            service.jpg_to_pdf([good, bad], tmp_path / "images.pdf")

    def test_jpg_to_pdf_raises_entrada_invalida_for_empty_image_file(
        self,
        tmp_path: Path,
        jpg_factory: Callable[..., Path],
        empty_file_factory: Callable[..., Path],
    ) -> None:
        good = jpg_factory("good.jpg")
        empty = empty_file_factory("empty.jpg")
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.jpg_to_pdf([good, empty], tmp_path / "images.pdf")

    def test_jpg_to_pdf_rejects_later_corrupt_image_without_orphaning_output_dir(
        self,
        tmp_path: Path,
        jpg_factory: Callable[..., Path],
        corrupt_jpg_factory: Callable[..., Path],
    ) -> None:
        """Regression: `jpg_to_pdf` must Pillow-`verify()` every image
        BEFORE `_make_output_dir`/`img2pdf.convert()` — otherwise a
        corrupt image late in `images` (after earlier valid ones were
        already checked) would leave an orphaned output directory."""
        first = jpg_factory("first.jpg")
        second = jpg_factory("second.jpg")
        bad = corrupt_jpg_factory("bad.jpg")
        service = PDFService()
        output = tmp_path / "out" / "images.pdf"

        with pytest.raises(EntradaInvalidaError):
            service.jpg_to_pdf([first, second, bad], output)

        assert not output.parent.exists()

    def test_jpg_to_pdf_rejects_img2pdf_convert_failure_without_orphaning_output_dir(
        self,
        tmp_path: Path,
        jpg_factory: Callable[..., Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression: `Image.verify()` only checks header/structure — it
        does not guarantee `img2pdf.convert()` will succeed. An image
        that passes `verify()` but fails conversion must still be
        rejected WITHOUT the output dir having been created, and without
        blaming a specific (possibly wrong) file in the error message."""
        first = jpg_factory("first.jpg")
        second = jpg_factory("second.jpg")
        service = PDFService()
        output = tmp_path / "out" / "images.pdf"

        def _raise(*_args: object, **_kwargs: object) -> bytes:
            raise img2pdf.ImageOpenError("simulated post-verify conversion failure")

        monkeypatch.setattr(img2pdf, "convert", _raise)

        with pytest.raises(EntradaInvalidaError, match="One or more input images"):
            service.jpg_to_pdf([first, second], output)

        assert not output.parent.exists()


class TestCompress:
    """Tests for `PDFService.compress`."""

    def test_compress_shrinks_image_heavy_pdf(
        self, tmp_path: Path, image_heavy_pdf_factory: Callable[..., Path]
    ) -> None:
        source = image_heavy_pdf_factory("heavy.pdf")
        service = PDFService()
        output = tmp_path / "compressed.pdf"

        result = service.compress(source, output)

        assert result == output
        assert output.exists()
        assert output.stat().st_size < source.stat().st_size

    def test_compress_logs_info_start_and_ok(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        image_heavy_pdf_factory: Callable[..., Path],
    ) -> None:
        source = image_heavy_pdf_factory("heavy.pdf")
        service = PDFService()
        output = tmp_path / "compressed.pdf"

        with caplog.at_level(logging.INFO, logger="app.core.services.pdf_service"):
            service.compress(source, output)

        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("start" in r.getMessage() for r in info_records)
        assert any("ok" in r.getMessage() for r in info_records)
        for record in info_records:
            assert str(tmp_path) not in record.getMessage()

    def test_compress_text_only_pdf_succeeds_without_raising(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        """A freshly `pikepdf`-serialized text-only PDF is NOT already at a
        structural fixed point — `pymupdf`'s `garbage=4, clean=True`
        object/xref cleanup measurably shrinks it (~35% smaller in local
        verification) even with zero embedded images, because the two
        libraries serialize PDF structure differently. That is a
        legitimate, spec-compliant size reduction (Requirement: Size
        Reduction), not the never-grow fallback — so this scenario
        verifies the general success contract (no exception, `Path`
        returned, output no bigger than source, INFO ok logged) rather
        than asserting byte-identical, which only the no-gain fallback
        path guarantees (see `test_compress_never_grows_...` below)."""
        source = valid_pdf_factory("text_only.pdf", pages=3)
        service = PDFService()
        output = tmp_path / "compressed.pdf"

        result = service.compress(source, output)

        assert result == output
        assert output.exists()
        assert output.stat().st_size <= source.stat().st_size

    def test_compress_never_grows_text_only_pdf_at_fixed_point_is_byte_identical(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        """Once a text-only PDF has already been through one `compress`
        pass, `pymupdf`'s recompression reaches a structural fixed point
        (verified empirically: a second pass produces an identical byte
        count) — recompressing it again must hit the never-grow fallback
        and copy the original bytes exactly."""
        original = valid_pdf_factory("text_only.pdf", pages=3)
        service = PDFService()
        once = tmp_path / "compressed_once.pdf"
        service.compress(original, once)

        twice = tmp_path / "compressed_twice.pdf"
        result = service.compress(once, twice)

        assert result == twice
        assert twice.read_bytes() == once.read_bytes()

    def test_compress_never_grows_already_optimized_pdf_is_byte_identical(
        self, tmp_path: Path, image_heavy_pdf_factory: Callable[..., Path]
    ) -> None:
        original = image_heavy_pdf_factory("heavy.pdf")
        service = PDFService()
        once = tmp_path / "compressed_once.pdf"
        service.compress(original, once)

        twice = tmp_path / "compressed_twice.pdf"
        result = service.compress(once, twice)

        assert result == twice
        assert twice.read_bytes() == once.read_bytes()

    def test_compress_raises_archivo_protegido_for_encrypted_input(
        self, tmp_path: Path, encrypted_pdf_factory: Callable[..., Path]
    ) -> None:
        source = encrypted_pdf_factory("locked.pdf", owner="o", user="u")
        service = PDFService()
        output = tmp_path / "compressed.pdf"

        with pytest.raises(ArchivoProtegidoError):
            service.compress(source, output)

        assert not output.exists()

    def test_compress_raises_archivo_protegido_for_owner_only_encrypted_input(
        self, tmp_path: Path, encrypted_pdf_factory: Callable[..., Path]
    ) -> None:
        """Regression: `pymupdf.Document.needs_pass`/`is_encrypted` both
        read False for owner-only encryption (blank user password) —
        empirically confirmed different from `pikepdf`, which correctly
        reports it as encrypted. `compress` must rely on the `pikepdf`
        check, not `pymupdf`'s signals, or it silently strips
        permissions-only protection during recompression."""
        source = encrypted_pdf_factory("owner_only.pdf", owner="owner-secret", user="")
        service = PDFService()
        output = tmp_path / "compressed.pdf"

        with pytest.raises(ArchivoProtegidoError):
            service.compress(source, output)

        assert not output.exists()

    def test_compress_raises_pdf_corrupto_naming_offending_file(
        self, tmp_path: Path, corrupt_pdf_factory: Callable[..., Path]
    ) -> None:
        source = corrupt_pdf_factory("corrupt.pdf")
        service = PDFService()

        with pytest.raises(PDFCorruptoError, match="corrupt.pdf"):
            service.compress(source, tmp_path / "compressed.pdf")

    def test_compress_raises_entrada_invalida_for_missing_input(self, tmp_path: Path) -> None:
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.compress(tmp_path / "does-not-exist.pdf", tmp_path / "compressed.pdf")

    def test_compress_raises_entrada_invalida_for_empty_input(
        self, tmp_path: Path, empty_file_factory: Callable[..., Path]
    ) -> None:
        source = empty_file_factory("empty.pdf")
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.compress(source, tmp_path / "compressed.pdf")

    def test_compress_raises_entrada_invalida_for_bad_output_dir(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf")
        service = PDFService()
        blocked_by_file = tmp_path / "blocked_by_file"
        blocked_by_file.write_bytes(b"not a directory")
        output = blocked_by_file / "sub" / "compressed.pdf"

        with pytest.raises(EntradaInvalidaError):
            service.compress(source, output)

    def test_compress_does_not_let_raw_pymupdf_exceptions_escape(
        self, tmp_path: Path, corrupt_pdf_factory: Callable[..., Path]
    ) -> None:
        source = corrupt_pdf_factory("corrupt.pdf")
        service = PDFService()

        with pytest.raises(PDFCorruptoError) as exc_info:
            service.compress(source, tmp_path / "compressed.pdf")

        assert not isinstance(
            exc_info.value, (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError)
        )


class TestAnchorPoint:
    """Direct tests for `PDFService._anchor_point`'s 5-branch coordinate math.

    `test_add_text_places_text_findable_at_expected_region` below only
    checks which QUADRANT the rendered text lands in — it would not catch
    a subtler off-by-margin or off-by-fontsize error. These tests assert
    exact `(x, y)` values against independently-computed expectations
    (not by re-deriving `_anchor_point`'s own formula), matching this
    file's convention of unit-testing private helpers directly (see
    `test_validate_pages_*` above).
    """

    _RECT = pymupdf.Rect(0, 0, 612, 792)  # Letter page, points, origin top-left
    _TEXT = "Hi"
    # pymupdf.get_text_length("Hi", fontname="helv", fontsize=11) == this
    # exact value (verified empirically, hardcoded so this test does not
    # re-derive the same measurement `_anchor_point` itself performs).
    _WIDTH = 10.384000062942505

    def test_top_left(self) -> None:
        service = PDFService()

        point = service._anchor_point("top-left", self._RECT, self._TEXT)

        assert point == pytest.approx((36, 47))

    def test_top_right(self) -> None:
        service = PDFService()

        point = service._anchor_point("top-right", self._RECT, self._TEXT)

        assert point == pytest.approx((612 - 36 - self._WIDTH, 47))

    def test_bottom_left(self) -> None:
        service = PDFService()

        point = service._anchor_point("bottom-left", self._RECT, self._TEXT)

        assert point == pytest.approx((36, 756))

    def test_bottom_right(self) -> None:
        service = PDFService()

        point = service._anchor_point("bottom-right", self._RECT, self._TEXT)

        assert point == pytest.approx((612 - 36 - self._WIDTH, 756))

    def test_center(self) -> None:
        service = PDFService()

        point = service._anchor_point("center", self._RECT, self._TEXT)

        assert point == pytest.approx((306 - self._WIDTH / 2, 396))

    def test_bottom_is_a_larger_y_than_top_matching_pymupdfs_y_grows_down_origin(
        self,
    ) -> None:
        """Regression guard: pymupdf's origin is top-left with y growing
        DOWN, so "bottom" must be a LARGER y than "top" — an inverted
        coordinate system would silently place text upside down relative
        to its intended anchor."""
        service = PDFService()

        top_point = service._anchor_point("top-left", self._RECT, self._TEXT)
        bottom_point = service._anchor_point("bottom-left", self._RECT, self._TEXT)

        assert bottom_point[1] > top_point[1]


class TestAddText:
    """Tests for `PDFService.add_text`."""

    @pytest.mark.parametrize(
        "position", ["top-left", "top-right", "bottom-left", "bottom-right", "center"]
    )
    def test_add_text_places_text_findable_at_expected_region(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path], position: str
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=1)
        service = PDFService()
        output = tmp_path / "edited.pdf"
        text = "Anchor marker"

        result = service.add_text(source, output, page=1, text=text, position=position)

        assert result == output
        doc = pymupdf.open(output)
        try:
            page = doc.load_page(0)
            rects = page.search_for(text)
            assert rects, f"text not found for position {position!r}"
            rect = rects[0]
            mid_x = (page.rect.x0 + page.rect.x1) / 2
            mid_y = (page.rect.y0 + page.rect.y1) / 2
            if "left" in position:
                assert rect.x0 < mid_x
            if "right" in position:
                assert rect.x0 > mid_x
            if "top" in position:
                assert rect.y1 < mid_y
            if "bottom" in position:
                assert rect.y0 > mid_y
            if position == "center":
                assert abs((rect.x0 + rect.x1) / 2 - mid_x) < 50
                assert abs((rect.y0 + rect.y1) / 2 - mid_y) < 50
        finally:
            doc.close()

    def test_add_text_raises_entrada_invalida_for_empty_text(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=1)
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.add_text(
                source, tmp_path / "edited.pdf", page=1, text="   ", position="top-left"
            )

    def test_add_text_raises_entrada_invalida_for_out_of_range_page(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=1)
        service = PDFService()

        with pytest.raises(EntradaInvalidaError, match="5"):
            service.add_text(
                source, tmp_path / "edited.pdf", page=5, text="hi", position="top-left"
            )

    def test_add_text_point_wins_over_position_regardless_of_preset(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        """`point` MUST win over `position` even when `position` is a
        different, otherwise-valid preset — `position` is not merely
        deprioritized, it is not consulted at all (spec's "point present
        overrides position" scenario)."""
        source = valid_pdf_factory("doc.pdf", pages=1)
        service = PDFService()
        output = tmp_path / "edited.pdf"
        text = "Point marker"
        point = (100.0, 200.0)

        result = service.add_text(
            source, output, page=1, text=text, position="center", point=point
        )

        assert result == output
        doc = pymupdf.open(output)
        try:
            page = doc.load_page(0)
            rects = page.search_for(text)
            assert rects, "text not found near the requested point"
            rect = rects[0]
            # insert_text's point is baseline-relative (see
            # `_anchor_point`'s docstring): the matched glyph box's
            # left edge sits at point[0], its bottom near point[1].
            # A "center" preset would have landed near (306, 396)
            # instead, well outside these tolerances.
            assert rect.x0 == pytest.approx(point[0], abs=5)
            assert rect.y1 == pytest.approx(point[1], abs=15)
        finally:
            doc.close()

    def test_add_text_point_none_falls_back_to_position(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        """`point=None` (the default) MUST derive the insertion point from
        `position`, exactly as before `point` existed."""
        source = valid_pdf_factory("doc.pdf", pages=1)
        service = PDFService()
        output = tmp_path / "edited.pdf"

        result = service.add_text(
            source, output, page=1, text="Preset marker", position="top-left"
        )

        assert result == output
        doc = pymupdf.open(output)
        try:
            rects = doc.load_page(0).search_for("Preset marker")
            assert rects
        finally:
            doc.close()


class TestRenderPage:
    """Tests for `PDFService.render_page`."""

    def test_render_page_fits_to_box_with_correct_zoom_image_size_and_origin(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        # 612x792 = valid_pdf_factory's underlying make_valid_pdf's fixed
        # Letter page size in points (same page dims TestAnchorPoint's
        # _RECT above uses).
        source = valid_pdf_factory("doc.pdf", pages=1)
        service = PDFService()
        max_w, max_h = 260, 280
        page_w, page_h = 612, 792

        result = service.render_page(source, page=1, max_w=max_w, max_h=max_h)

        assert isinstance(result, PagePreviewResult)
        expected_zoom = min(max_w / page_w, max_h / page_h)
        assert result.zoom == pytest.approx(expected_zoom)
        assert result.origin == pytest.approx((0.0, 0.0))
        # pymupdf's own point->pixel rounding can land 1px off Python's
        # round() (confirmed empirically: round(612*zoom)=216 but the
        # real pixmap is 217px wide at this exact zoom) — abs=1 tolerates
        # that real discrepancy without masking an actual off-by-several
        # rounding bug.
        assert result.image.width == pytest.approx(round(page_w * expected_zoom), abs=1)
        assert result.image.height == pytest.approx(round(page_h * expected_zoom), abs=1)

    def test_render_page_raises_entrada_invalida_for_out_of_range_page(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=1)
        service = PDFService()

        with pytest.raises(EntradaInvalidaError, match="5"):
            service.render_page(source, page=5, max_w=260, max_h=280)

    def test_render_page_raises_entrada_invalida_for_empty_source(
        self, tmp_path: Path, empty_file_factory: Callable[..., Path]
    ) -> None:
        source = empty_file_factory("empty.pdf")
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.render_page(source, page=1, max_w=260, max_h=280)

    def test_render_page_raises_pdf_corrupto_for_corrupt_source(
        self, tmp_path: Path, corrupt_pdf_factory: Callable[..., Path]
    ) -> None:
        source = corrupt_pdf_factory("corrupt.pdf")
        service = PDFService()

        with pytest.raises(PDFCorruptoError) as exc_info:
            service.render_page(source, page=1, max_w=260, max_h=280)

        # No raw pymupdf exception escapes: `PDFCorruptoError` is a
        # domain exception, unrelated to pymupdf's exception hierarchy.
        assert not isinstance(exc_info.value, (pymupdf.FileDataError, pymupdf.EmptyFileError))

    @pytest.mark.parametrize(
        ("max_w", "max_h"),
        [(0, 280), (260, 0), (-10, 280), (260, -10)],
    )
    def test_render_page_raises_entrada_invalida_for_non_positive_box(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path], max_w: int, max_h: int
    ) -> None:
        """A Tk widget's winfo_width()/winfo_height() return 0 (or 1)
        before it's first realized — a real caller-triggerable case, not
        a hypothetical one, since this method exists to feed a preview
        widget's image. Zero/negative box dimensions must raise here
        rather than silently producing a zoom=0.0/0x0 image."""
        source = valid_pdf_factory("doc.pdf", pages=1)
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.render_page(source, page=1, max_w=max_w, max_h=max_h)


class TestHighlightText:
    """Tests for `PDFService.highlight_text`."""

    def test_highlight_text_single_page_match_leaves_other_pages_untouched(
        self, tmp_path: Path, text_pdf_with_matches_factory: Callable[..., Path]
    ) -> None:
        source = text_pdf_with_matches_factory(
            "doc.pdf", pages_text=["invoice invoice", "nothing here", "total due"]
        )
        service = PDFService()
        output = tmp_path / "highlighted.pdf"

        result = service.highlight_text(source, output, query="invoice", page=1)

        assert result == output
        doc = pymupdf.open(output)
        try:
            assert len(list(doc.load_page(0).annots())) == 2
            assert len(list(doc.load_page(1).annots())) == 0
        finally:
            doc.close()

    def test_highlight_text_all_pages_scope_spans_multiple_pages(
        self, tmp_path: Path, text_pdf_with_matches_factory: Callable[..., Path]
    ) -> None:
        source = text_pdf_with_matches_factory(
            "doc.pdf", pages_text=["total amount", "nothing here", "total due"]
        )
        service = PDFService()
        output = tmp_path / "highlighted.pdf"

        service.highlight_text(source, output, query="total", page=None)

        doc = pymupdf.open(output)
        try:
            assert len(list(doc.load_page(0).annots())) == 1
            assert len(list(doc.load_page(1).annots())) == 0
            assert len(list(doc.load_page(2).annots())) == 1
        finally:
            doc.close()

    def test_highlight_text_zero_match_on_specific_page_raises(
        self, tmp_path: Path, text_pdf_with_matches_factory: Callable[..., Path]
    ) -> None:
        source = text_pdf_with_matches_factory(
            "doc.pdf", pages_text=["invoice invoice", "nothing here", "total due"]
        )
        service = PDFService()
        output = tmp_path / "highlighted.pdf"

        with pytest.raises(PDFSinCoincidenciasError):
            service.highlight_text(source, output, query="nonexistent", page=2)

        assert not output.exists()

    def test_highlight_text_zero_match_across_all_pages_raises(
        self, tmp_path: Path, text_pdf_with_matches_factory: Callable[..., Path]
    ) -> None:
        source = text_pdf_with_matches_factory(
            "doc.pdf", pages_text=["invoice invoice", "nothing here", "total due"]
        )
        service = PDFService()
        output = tmp_path / "highlighted.pdf"

        with pytest.raises(PDFSinCoincidenciasError):
            service.highlight_text(source, output, query="nonexistent", page=None)

        assert not output.exists()

    def test_highlight_text_raises_entrada_invalida_for_empty_query(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=1)
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.highlight_text(source, tmp_path / "highlighted.pdf", query="   ")

    def test_highlight_text_raises_entrada_invalida_for_out_of_range_page(
        self, tmp_path: Path, text_pdf_with_matches_factory: Callable[..., Path]
    ) -> None:
        source = text_pdf_with_matches_factory("doc.pdf", pages_text=["invoice"])
        service = PDFService()

        with pytest.raises(EntradaInvalidaError, match="9"):
            service.highlight_text(source, tmp_path / "highlighted.pdf", query="invoice", page=9)

    def test_highlight_annotation_survives_save_and_reload(
        self, tmp_path: Path, text_pdf_with_matches_factory: Callable[..., Path]
    ) -> None:
        source = text_pdf_with_matches_factory("doc.pdf", pages_text=["invoice"])
        service = PDFService()
        output = tmp_path / "highlighted.pdf"

        service.highlight_text(source, output, query="invoice", page=1)

        doc = pymupdf.open(output)
        try:
            page = doc.load_page(0)
            annots = list(page.annots())
            assert len(annots) == 1
            assert annots[0].type[1] == "Highlight"
        finally:
            doc.close()


class TestRedactText:
    """Tests for `PDFService.redact_text`."""

    def test_redact_text_single_page_match_is_gone_from_extracted_text(
        self, tmp_path: Path, text_pdf_with_matches_factory: Callable[..., Path]
    ) -> None:
        """Spec scenario 'Matched text is unrecoverable after redaction' —
        verifies real permanence via reload + `get_text()`, not just that
        the method returned without raising."""
        source = text_pdf_with_matches_factory(
            "doc.pdf", pages_text=["confidential data", "nothing here", "total due"]
        )
        service = PDFService()
        output = tmp_path / "redacted.pdf"

        result = service.redact_text(source, output, query="confidential", page=1)

        assert result == output
        doc = pymupdf.open(output)
        try:
            page1 = doc.load_page(0)
            assert page1.search_for("confidential") == []
            assert "confidential" not in page1.get_text().lower()
            assert len(list(doc.load_page(1).annots())) == 0
            assert "total due" in doc.load_page(2).get_text().lower()
        finally:
            doc.close()

    def test_redact_text_all_pages_scope_spans_multiple_pages(
        self, tmp_path: Path, text_pdf_with_matches_factory: Callable[..., Path]
    ) -> None:
        source = text_pdf_with_matches_factory(
            "doc.pdf", pages_text=["total amount", "nothing here", "total due"]
        )
        service = PDFService()
        output = tmp_path / "redacted.pdf"

        service.redact_text(source, output, query="total", page=None)

        doc = pymupdf.open(output)
        try:
            for idx in range(3):
                assert "total" not in doc.load_page(idx).get_text().lower()
        finally:
            doc.close()

    def test_redact_text_zero_match_on_specific_page_raises(
        self, tmp_path: Path, text_pdf_with_matches_factory: Callable[..., Path]
    ) -> None:
        source = text_pdf_with_matches_factory(
            "doc.pdf", pages_text=["invoice invoice", "nothing here", "total due"]
        )
        service = PDFService()
        output = tmp_path / "redacted.pdf"

        with pytest.raises(PDFSinCoincidenciasError):
            service.redact_text(source, output, query="nonexistent", page=2)

        assert not output.exists()

    def test_redact_text_zero_match_across_all_pages_raises(
        self, tmp_path: Path, text_pdf_with_matches_factory: Callable[..., Path]
    ) -> None:
        source = text_pdf_with_matches_factory(
            "doc.pdf", pages_text=["invoice invoice", "nothing here", "total due"]
        )
        service = PDFService()
        output = tmp_path / "redacted.pdf"

        with pytest.raises(PDFSinCoincidenciasError):
            service.redact_text(source, output, query="nonexistent", page=None)

        assert not output.exists()

    def test_redact_text_raises_entrada_invalida_for_empty_query(
        self, tmp_path: Path, valid_pdf_factory: Callable[..., Path]
    ) -> None:
        source = valid_pdf_factory("doc.pdf", pages=1)
        service = PDFService()

        with pytest.raises(EntradaInvalidaError):
            service.redact_text(source, tmp_path / "redacted.pdf", query="")

    def test_redact_text_raises_entrada_invalida_for_out_of_range_page(
        self, tmp_path: Path, text_pdf_with_matches_factory: Callable[..., Path]
    ) -> None:
        source = text_pdf_with_matches_factory("doc.pdf", pages_text=["confidential"])
        service = PDFService()

        with pytest.raises(EntradaInvalidaError, match="9"):
            service.redact_text(
                source, tmp_path / "redacted.pdf", query="confidential", page=9
            )


class TestCrossCuttingLoggingAndExceptionContainment:
    """Cross-cutting tests (4.3/4.4) exercised across all seven pre-existing
    operations, plus a dedicated containment test for the three new
    `edit-pdf` PR1 text operations (`add_text`/`highlight_text`/
    `redact_text`) below.

    Verifies every operation logs INFO on success and WARNING on
    failure via the Sprint 0 `get_logger` (filename-only messages, no
    absolute paths, no passwords), and that no raw
    `pikepdf`/`img2pdf`/`Pillow`/`pymupdf` exception ever surfaces to a
    caller — only the domain exceptions do.
    """

    _RAW_LIBRARY_EXCEPTIONS = (
        pikepdf.PdfError,
        pikepdf.PasswordError,
        UnidentifiedImageError,
        OSError,
        img2pdf.ImageOpenError,
        img2pdf.AlphaChannelError,
        pymupdf.FileDataError,
        pymupdf.EmptyFileError,
    )

    def test_all_six_ops_log_info_on_success(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        valid_pdf_factory: Callable[..., Path],
        encrypted_pdf_factory: Callable[..., Path],
        jpg_factory: Callable[..., Path],
    ) -> None:
        service = PDFService()
        good = valid_pdf_factory("good.pdf", pages=2)
        encrypted = encrypted_pdf_factory("locked.pdf", owner="owner-pwd", user="user-pwd")
        image = jpg_factory("image.jpg")

        success_calls: dict[str, Callable[[], object]] = {
            "merge": lambda: service.merge([good], tmp_path / "merge_ok.pdf"),
            "split": lambda: service.split(good, tmp_path / "split_ok"),
            "organize": lambda: service.organize(good, tmp_path / "organize_ok.pdf", order=[1]),
            "protect": lambda: service.protect(
                good, tmp_path / "protect_ok.pdf", owner_password="secret"
            ),
            "unlock": lambda: service.unlock(
                encrypted, tmp_path / "unlock_ok.pdf", password="user-pwd"
            ),
            "jpg_to_pdf": lambda: service.jpg_to_pdf([image], tmp_path / "jpg_ok.pdf"),
            "compress": lambda: service.compress(good, tmp_path / "compress_ok.pdf"),
        }

        with caplog.at_level(logging.INFO, logger="app.core.services.pdf_service"):
            for op_name, call in success_calls.items():
                caplog.clear()
                call()

                info_records = [r for r in caplog.records if r.levelno == logging.INFO]
                assert info_records, f"{op_name} logged no INFO record on success"
                assert any("ok" in r.getMessage() for r in info_records), (
                    f"{op_name} INFO log missing an 'ok' success marker"
                )
                for record in info_records:
                    message = record.getMessage()
                    assert str(tmp_path) not in message, (
                        f"{op_name} INFO log leaked an absolute path"
                    )
                    assert "secret" not in message, f"{op_name} INFO log leaked a password"

    def test_all_six_ops_log_warning_and_raise_only_domain_exceptions_on_failure(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        encrypted_pdf_factory: Callable[..., Path],
        corrupt_pdf_factory: Callable[..., Path],
        corrupt_jpg_factory: Callable[..., Path],
    ) -> None:
        service = PDFService()
        encrypted = encrypted_pdf_factory("locked.pdf", owner="owner-pwd", user="user-pwd")
        corrupt = corrupt_pdf_factory("corrupt.pdf")
        corrupt_image = corrupt_jpg_factory("corrupt.jpg")

        failure_calls: dict[str, tuple[Callable[[], object], type[Exception]]] = {
            "merge": (
                lambda: service.merge([corrupt], tmp_path / "merge_bad.pdf"),
                PDFCorruptoError,
            ),
            "split": (
                lambda: service.split(corrupt, tmp_path / "split_bad"),
                PDFCorruptoError,
            ),
            "organize": (
                lambda: service.organize(corrupt, tmp_path / "organize_bad.pdf", order=[1]),
                PDFCorruptoError,
            ),
            "protect": (
                lambda: service.protect(
                    encrypted, tmp_path / "protect_bad.pdf", owner_password="secret"
                ),
                ArchivoProtegidoError,
            ),
            "unlock": (
                lambda: service.unlock(
                    encrypted, tmp_path / "unlock_bad.pdf", password="not-the-password"
                ),
                ContrasenaInvalidaError,
            ),
            "jpg_to_pdf": (
                lambda: service.jpg_to_pdf([corrupt_image], tmp_path / "jpg_bad.pdf"),
                EntradaInvalidaError,
            ),
            "compress": (
                lambda: service.compress(corrupt, tmp_path / "compress_bad.pdf"),
                PDFCorruptoError,
            ),
        }

        with caplog.at_level(logging.WARNING, logger="app.core.services.pdf_service"):
            for op_name, (call, expected_exc) in failure_calls.items():
                caplog.clear()
                with pytest.raises(expected_exc) as exc_info:
                    call()

                assert not isinstance(exc_info.value, self._RAW_LIBRARY_EXCEPTIONS), (
                    f"{op_name} let a raw library exception surface as {type(exc_info.value)!r}"
                )

                warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
                assert warning_records, f"{op_name} logged no WARNING record on failure"
                for record in warning_records:
                    message = record.getMessage()
                    assert str(tmp_path) not in message, (
                        f"{op_name} WARNING log leaked an absolute path"
                    )
                    assert "secret" not in message, f"{op_name} WARNING log leaked a password"
                    assert "not-the-password" not in message, (
                        f"{op_name} WARNING log leaked a password"
                    )

    def test_all_validation_only_failures_log_warning_before_raising(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        valid_pdf_factory: Callable[..., Path],
        encrypted_pdf_factory: Callable[..., Path],
    ) -> None:
        """Regression for the verify CRITICAL finding: direct-`raise`
        validation sites (never routed through `_translate_errors`) must
        also log a WARNING before propagating. Covers all ten call sites
        named in the finding, across all six operations — not just the
        library-boundary failures `test_all_six_ops_log_warning_...`
        already exercises."""
        service = PDFService()
        two_page = valid_pdf_factory("two_page.pdf", pages=2)
        owner_only_locked = encrypted_pdf_factory("owner_only.pdf", owner="owner-secret", user="")

        blocked_by_file = tmp_path / "blocked_by_file"
        blocked_by_file.write_bytes(b"not a directory")

        validation_failures: dict[str, tuple[Callable[[], object], type[Exception]]] = {
            "_require_nonempty_file (missing input)": (
                lambda: service.merge(
                    [tmp_path / "does-not-exist.pdf"], tmp_path / "merge_missing.pdf"
                ),
                EntradaInvalidaError,
            ),
            "_validate_pages (out-of-range)": (
                lambda: service.split(two_page, tmp_path / "split_oor", ranges=[(1, 5)]),
                EntradaInvalidaError,
            ),
            "_make_output_dir (cannot create)": (
                lambda: service.merge([two_page], blocked_by_file / "sub" / "out.pdf"),
                EntradaInvalidaError,
            ),
            "merge (zero files)": (
                lambda: service.merge([], tmp_path / "merge_zero.pdf"),
                EntradaInvalidaError,
            ),
            "split (start > end)": (
                lambda: service.split(two_page, tmp_path / "split_bad_range", ranges=[(2, 1)]),
                EntradaInvalidaError,
            ),
            "organize (duplicate index)": (
                lambda: service.organize(two_page, tmp_path / "organize_dup.pdf", order=[1, 1]),
                EntradaInvalidaError,
            ),
            "protect (empty password)": (
                lambda: service.protect(
                    two_page, tmp_path / "protect_empty_pwd.pdf", owner_password="   "
                ),
                EntradaInvalidaError,
            ),
            "protect (owner-only-encryption guard)": (
                lambda: service.protect(
                    owner_only_locked,
                    tmp_path / "protect_reencrypt.pdf",
                    owner_password="secret",
                ),
                ArchivoProtegidoError,
            ),
            "unlock (not encrypted)": (
                lambda: service.unlock(
                    two_page, tmp_path / "unlock_not_encrypted.pdf", password="whatever"
                ),
                EntradaInvalidaError,
            ),
            "jpg_to_pdf (zero images)": (
                lambda: service.jpg_to_pdf([], tmp_path / "jpg_zero.pdf"),
                EntradaInvalidaError,
            ),
        }

        with caplog.at_level(logging.WARNING, logger="app.core.services.pdf_service"):
            for site_name, (call, expected_exc) in validation_failures.items():
                caplog.clear()
                with pytest.raises(expected_exc):
                    call()

                warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
                assert warning_records, (
                    f"{site_name} raised {expected_exc.__name__} but logged no WARNING record"
                )
                for record in warning_records:
                    message = record.getMessage()
                    assert str(tmp_path) not in message, (
                        f"{site_name} WARNING log leaked an absolute path"
                    )
                    assert "secret" not in message, f"{site_name} WARNING log leaked a password"

    def test_new_text_editing_ops_do_not_let_raw_pymupdf_exceptions_escape(
        self, tmp_path: Path, corrupt_pdf_factory: Callable[..., Path]
    ) -> None:
        """Mirrors `test_compress_does_not_let_raw_pymupdf_exceptions_escape`
        for the three `edit-pdf` PR1 text operations."""
        source = corrupt_pdf_factory("corrupt.pdf")
        service = PDFService()

        calls: dict[str, Callable[[], object]] = {
            "add_text": lambda: service.add_text(
                source, tmp_path / "add_text_bad.pdf", page=1, text="hi", position="top-left"
            ),
            "highlight_text": lambda: service.highlight_text(
                source, tmp_path / "highlight_bad.pdf", query="hi"
            ),
            "redact_text": lambda: service.redact_text(
                source, tmp_path / "redact_bad.pdf", query="hi"
            ),
        }

        for op_name, call in calls.items():
            with pytest.raises(PDFCorruptoError) as exc_info:
                call()

            assert not isinstance(exc_info.value, self._RAW_LIBRARY_EXCEPTIONS), (
                f"{op_name} let a raw library exception surface as {type(exc_info.value)!r}"
            )
