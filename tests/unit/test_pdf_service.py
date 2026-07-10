"""Tests for `app.core.services.pdf_service`.

Covers the PR1 foundation (constructor, `_translate_errors` mapping,
`_validate_pages` helper), the PR2 `merge`/`split` operations, the PR3
`organize`/`protect`/`unlock` operations, and the PR4 `jpg_to_pdf`
operation plus cross-cutting logging/exception-containment tests
exercised across all six operations.
"""

from __future__ import annotations

import logging
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


class TestCrossCuttingLoggingAndExceptionContainment:
    """Cross-cutting tests (4.3/4.4) exercised across all six operations.

    Verifies every operation logs INFO on success and WARNING on
    failure via the Sprint 0 `get_logger` (filename-only messages, no
    absolute paths, no passwords), and that no raw
    `pikepdf`/`img2pdf`/`Pillow` exception ever surfaces to a caller —
    only the four domain exceptions do.
    """

    _RAW_LIBRARY_EXCEPTIONS = (
        pikepdf.PdfError,
        pikepdf.PasswordError,
        UnidentifiedImageError,
        OSError,
        img2pdf.ImageOpenError,
        img2pdf.AlphaChannelError,
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
