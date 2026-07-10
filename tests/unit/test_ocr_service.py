"""Tests for `app.core.services.ocr_service` — PR3 scope.

`OCRService`'s full behavioral contract is verified with a fake
`OCRProvider` (see spec's "Provider-Agnostic Testability" requirement) —
no real Tesseract is exercised here. `TesseractOCRProvider`'s own real
recognition/timeout behavior is covered separately by
`tests/integration/test_tesseract_ocr_provider.py`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pikepdf
import pymupdf
import pytest
from PIL import Image

from app.core.exceptions import (
    EntradaInvalidaError,
    OCRFallidaError,
    OCRNoDisponibleError,
)
from app.core.providers.azure_ocr_provider import AzureOCRProvider
from app.core.providers.ocr_provider import OCRProvider, RecognizedWord
from app.core.providers.tesseract_ocr_provider import TesseractOCRProvider
from app.core.services.ocr_service import OCRService

_LOGGER_NAME = "app.core.services.ocr_service"

_DEFAULT_WORD = RecognizedWord(text="Factura", left=100, top=100, width=140, height=40)


class FakeOCRProvider:
    """Configurable fake `OCRProvider` for `OCRService` tests.

    `behavior` selects what `reconocer()` does on each call:
        "succeed" -> returns `words` (default: `_DEFAULT_WORD`)
        "unavailable" -> raises `OCRNoDisponibleError`
        "fail" -> raises a generic `RuntimeError` (simulates a raw
            `pytesseract`/Tesseract failure `OCRService` must translate)
        "timeout" -> raises `TimeoutError`
        "not_implemented" -> raises `NotImplementedError` (Azure stub shape)
        "fail_on_second_call" -> succeeds with `words` on the FIRST call,
            raises `RuntimeError` on every call after — used to exercise
            the mid-recognition, no-orphan-output invariant on a
            multi-page source
    """

    def __init__(
        self, behavior: str = "succeed", words: list[RecognizedWord] | None = None
    ) -> None:
        self.behavior = behavior
        self.words = words if words is not None else [_DEFAULT_WORD]
        self.called = False
        self.call_count = 0

    def reconocer(self, image: Image.Image) -> list[RecognizedWord]:
        self.called = True
        self.call_count += 1
        if self.behavior == "succeed":
            return self.words
        if self.behavior == "unavailable":
            raise OCRNoDisponibleError("fake provider unavailable")
        if self.behavior == "fail":
            raise RuntimeError("fake raw recognition failure")
        if self.behavior == "timeout":
            raise TimeoutError("fake recognition timed out")
        if self.behavior == "not_implemented":
            raise NotImplementedError("fake provider not implemented")
        if self.behavior == "fail_on_second_call":
            if self.call_count == 1:
                return self.words
            raise RuntimeError("fake raw recognition failure on page 2")
        raise AssertionError(f"unknown behavior: {self.behavior!r}")

    def esta_disponible(self) -> tuple[bool, str]:
        return True, "fake provider always available"


def _merge_two_page_pdf(page1: Path, page2: Path, output: Path) -> Path:
    """Merge two 1-page PDFs into a 2-page PDF at `output`.

    Used only to build a multi-page image-only source for the
    no-orphan-output test — `image_pdf_factory.make_image_only_pdf`'s
    signature deliberately produces a single page per call (per spec), so
    a 2-page source is assembled from two single-page fixtures instead of
    widening that factory's contract.
    """
    merged = pikepdf.Pdf.new()
    with pikepdf.Pdf.open(page1) as pdf1:
        merged.pages.extend(pdf1.pages)
    with pikepdf.Pdf.open(page2) as pdf2:
        merged.pages.extend(pdf2.pages)
    merged.save(output)
    return output


class TestProviderSelection:
    def test_default_provider_is_tesseract_when_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OCR_PROVIDER", raising=False)

        service = OCRService()

        assert isinstance(service._provider, TesseractOCRProvider)

    def test_env_selects_azure_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCR_PROVIDER", "azure_di")

        service = OCRService()

        assert isinstance(service._provider, AzureOCRProvider)

    def test_env_change_after_construction_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCR_PROVIDER", "tesseract")
        service = OCRService()

        monkeypatch.setenv("OCR_PROVIDER", "azure_di")

        assert isinstance(service._provider, TesseractOCRProvider)

    def test_explicit_provider_overrides_factory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCR_PROVIDER", "azure_di")
        fake = FakeOCRProvider("succeed")

        service = OCRService(provider=fake)

        assert service._provider is fake


class TestValidation:
    def test_rejects_non_pdf_extension_before_provider_invoked(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = FakeOCRProvider("succeed")
        service = OCRService(provider=fake)
        source = tmp_path / "not_a_pdf.txt"
        source.write_text("hello")

        with (
            caplog.at_level(logging.WARNING, logger=_LOGGER_NAME),
            pytest.raises(EntradaInvalidaError),
        ):
            service.ocr(source, tmp_path / "out.pdf")

        assert fake.called is False
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 1
        assert "not_a_pdf.txt" in warning_records[0].getMessage()
        assert str(tmp_path) not in warning_records[0].getMessage()

    def test_rejects_missing_source(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = FakeOCRProvider("succeed")
        service = OCRService(provider=fake)
        source = tmp_path / "missing.pdf"

        with (
            caplog.at_level(logging.WARNING, logger=_LOGGER_NAME),
            pytest.raises(EntradaInvalidaError),
        ):
            service.ocr(source, tmp_path / "out.pdf")

        assert fake.called is False

    def test_rejects_empty_source(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = FakeOCRProvider("succeed")
        service = OCRService(provider=fake)
        source = tmp_path / "empty.pdf"
        source.write_bytes(b"")

        with (
            caplog.at_level(logging.WARNING, logger=_LOGGER_NAME),
            pytest.raises(EntradaInvalidaError),
        ):
            service.ocr(source, tmp_path / "out.pdf")

        assert fake.called is False

    def test_rejects_uncreatable_output_dir(
        self,
        tmp_path: Path,
        image_only_pdf_factory: Callable[..., Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fake = FakeOCRProvider("succeed")
        service = OCRService(provider=fake)
        source = image_only_pdf_factory("scan.pdf")
        blocked_by_file = tmp_path / "blocked_by_file"
        blocked_by_file.write_bytes(b"not a directory")
        output = blocked_by_file / "sub" / "out.pdf"

        with (
            caplog.at_level(logging.WARNING, logger=_LOGGER_NAME),
            pytest.raises(EntradaInvalidaError),
        ):
            service.ocr(source, output)

        assert fake.called is False


class TestHappyPathAndAlignment:
    def test_ocr_returns_output_path_and_logs(
        self,
        tmp_path: Path,
        image_only_pdf_factory: Callable[..., Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fake = FakeOCRProvider("succeed")
        service = OCRService(provider=fake)
        source = image_only_pdf_factory("scan.pdf")
        output = tmp_path / "out.pdf"

        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            result = service.ocr(source, output)

        assert result == output
        assert output.exists()
        assert fake.called is True
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("start" in r.getMessage() for r in info_records)
        assert any("ok" in r.getMessage() for r in info_records)
        for record in info_records:
            assert str(tmp_path) not in record.getMessage()

    def test_recognized_word_is_positionally_aligned_in_output(
        self, tmp_path: Path, image_only_pdf_factory: Callable[..., Path]
    ) -> None:
        word = RecognizedWord(text="Factura", left=100, top=100, width=140, height=40)
        fake = FakeOCRProvider("succeed", words=[word])
        service = OCRService(provider=fake)
        source = image_only_pdf_factory("scan.pdf")
        output = tmp_path / "out.pdf"

        service.ocr(source, output)

        # Reopen fresh (not reusing any in-memory document) per design's
        # own empirical verification method.
        doc = pymupdf.open(output)
        try:
            page = doc[0]
            assert "Factura" in page.get_text()

            # Mirrors design's own empirical alignment check
            # (`sdd/ocr-pdf-provider/design`, "Empirical status" (b)),
            # re-verified directly against this repo's `insert_text`
            # behavior rather than assumed: `point=(left*72/300,
            # (top+height)*72/300)` is documented/observed as the
            # bottom-left of the inserted text's bbox, so the rendered
            # word's bbox BOTTOM edge (y1, not y0 — "Factura" has no
            # descenders) is what lands near that point, confirmed here
            # to ~3pt with the default "helv" font at this fontsize.
            expected_x = word.left * 72 / 300
            expected_y = (word.top + word.height) * 72 / 300
            tolerance = 5.0

            matches = [w for w in page.get_text("words") if w[4] == "Factura"]
            assert matches, "expected word 'Factura' not found via get_text('words')"
            x0, _y0, _x1, y1, _text, *_ = matches[0]
            assert abs(x0 - expected_x) < tolerance
            assert abs(y1 - expected_y) < tolerance
        finally:
            doc.close()


class TestProviderErrorTranslation:
    def test_unavailable_provider_raises_ocr_no_disponible(
        self, tmp_path: Path, image_only_pdf_factory: Callable[..., Path]
    ) -> None:
        fake = FakeOCRProvider("unavailable")
        service = OCRService(provider=fake)
        source = image_only_pdf_factory("scan.pdf")

        with pytest.raises(OCRNoDisponibleError):
            service.ocr(source, tmp_path / "out.pdf")

    def test_generic_provider_failure_raises_ocr_fallida(
        self, tmp_path: Path, image_only_pdf_factory: Callable[..., Path]
    ) -> None:
        fake = FakeOCRProvider("fail")
        service = OCRService(provider=fake)
        source = image_only_pdf_factory("scan.pdf")

        with pytest.raises(OCRFallidaError):
            service.ocr(source, tmp_path / "out.pdf")

    def test_timeout_raises_ocr_fallida(
        self, tmp_path: Path, image_only_pdf_factory: Callable[..., Path]
    ) -> None:
        fake = FakeOCRProvider("timeout")
        service = OCRService(provider=fake)
        source = image_only_pdf_factory("scan.pdf")

        with pytest.raises(OCRFallidaError):
            service.ocr(source, tmp_path / "out.pdf")

    def test_provider_failure_logs_warning_filename_only(
        self,
        tmp_path: Path,
        image_only_pdf_factory: Callable[..., Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fake = FakeOCRProvider("fail")
        service = OCRService(provider=fake)
        source = image_only_pdf_factory("scan.pdf")

        with (
            caplog.at_level(logging.WARNING, logger=_LOGGER_NAME),
            pytest.raises(OCRFallidaError),
        ):
            service.ocr(source, tmp_path / "out.pdf")

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("scan.pdf" in r.getMessage() for r in warning_records)
        for record in warning_records:
            assert str(tmp_path) not in record.getMessage()

    def test_raw_provider_exception_never_escapes(
        self, tmp_path: Path, image_only_pdf_factory: Callable[..., Path]
    ) -> None:
        fake = FakeOCRProvider("fail")
        service = OCRService(provider=fake)
        source = image_only_pdf_factory("scan.pdf")

        with pytest.raises(OCRFallidaError) as exc_info:
            service.ocr(source, tmp_path / "out.pdf")

        assert type(exc_info.value) is OCRFallidaError

    def test_provider_failure_leaves_no_output_file(
        self, tmp_path: Path, image_only_pdf_factory: Callable[..., Path]
    ) -> None:
        fake = FakeOCRProvider("fail")
        service = OCRService(provider=fake)
        source = image_only_pdf_factory("scan.pdf")
        output = tmp_path / "out.pdf"

        with pytest.raises(OCRFallidaError):
            service.ocr(source, output)

        assert not output.exists()

    def test_provider_failure_mid_recognition_leaves_no_orphan_output(
        self, tmp_path: Path, image_only_pdf_factory: Callable[..., Path]
    ) -> None:
        """Validate-then-write: page 1 succeeds, page 2 fails -> no output at all.

        Locks in the invariant that a mid-recognition failure on ANY page
        of a multi-page source must never leave a partial/corrupt output
        PDF behind — `doc.save(output)` only runs after every page has
        been recognized and overlaid successfully.
        """
        page1 = image_only_pdf_factory("page1.pdf", text="Uno")
        page2 = image_only_pdf_factory("page2.pdf", text="Dos")
        source = tmp_path / "two_pages.pdf"
        _merge_two_page_pdf(page1, page2, source)

        fake = FakeOCRProvider("fail_on_second_call")
        service = OCRService(provider=fake)
        output = tmp_path / "out.pdf"

        with pytest.raises(OCRFallidaError):
            service.ocr(source, output)

        assert fake.call_count == 2
        assert not output.exists()

    def test_corrupt_pdf_raises_ocr_fallida_not_a_raw_pymupdf_exception(
        self,
        tmp_path: Path,
        corrupt_pdf_factory: Callable[..., Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Regression: a structurally corrupt (but non-empty) `.pdf` must
        be translated to `OCRFallidaError`, not let a raw
        `pymupdf.FileDataError` escape with the absolute source path
        embedded in its message. `_require_nonempty_pdf` only checks
        extension and size, not parseability, so `pymupdf.open` is the
        only thing that can actually detect this — `_translate_provider_
        errors` must wrap that call, not just `provider.reconocer()`."""
        fake = FakeOCRProvider("succeed")
        service = OCRService(provider=fake)
        source = corrupt_pdf_factory("corrupt.pdf")
        output = tmp_path / "out.pdf"

        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME), pytest.raises(
            OCRFallidaError
        ) as exc_info:
            service.ocr(source, output)

        assert type(exc_info.value) is OCRFallidaError
        assert not output.exists()
        assert fake.call_count == 0

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_records
        for record in warning_records:
            assert str(tmp_path) not in record.getMessage()


class TestAzureStub:
    def test_azure_provider_ocr_propagates_not_implemented(
        self, tmp_path: Path, image_only_pdf_factory: Callable[..., Path]
    ) -> None:
        service = OCRService(provider=AzureOCRProvider())
        source = image_only_pdf_factory("scan.pdf")

        with pytest.raises(NotImplementedError):
            service.ocr(source, tmp_path / "out.pdf")

    def test_azure_selected_via_env_propagates_not_implemented(
        self,
        tmp_path: Path,
        image_only_pdf_factory: Callable[..., Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OCR_PROVIDER", "azure_di")
        service = OCRService()
        source = image_only_pdf_factory("scan.pdf")

        with pytest.raises(NotImplementedError):
            service.ocr(source, tmp_path / "out.pdf")


def test_fake_provider_satisfies_the_protocol() -> None:
    fake = FakeOCRProvider("succeed")

    assert isinstance(fake, OCRProvider)
