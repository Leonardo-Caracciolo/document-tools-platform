"""Tests for `app.core.services.scan_service` — PR2 scope.

`ScanService` is the FIRST cross-service composition in this codebase, so
these tests exercise TWO layers deliberately:

- Happy-path/composition tests use a REAL `PDFService()` (no external
  binary, safe to run for real) and a REAL `OCRService(provider=
  FakeOCRProvider(...))` — real orchestration logic, but no dependency on
  an actually-installed Tesseract binary (mirrors `test_ocr_service.py`'s
  own fake-provider approach, since this project already splits real-
  Tesseract coverage into `tests/integration/test_tesseract_ocr_provider.py`
  for CI-availability reasons).
- Error-injection tests (domain exception propagation, composition call
  order) use a fully fake `pdf_service`/`ocr_service` pair to force
  failures and record call order deterministically.

`deskew_fn` is always a configurable fake (`FakeDeskewFn`) — the real
`deskew_and_crop` pixel math is already independently covered by
`test_deskew.py` (PR1); these tests only need to verify `ScanService`
calls it correctly and reacts correctly to its `bool` return / raised
exceptions.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from app.core.exceptions import EntradaInvalidaError, OCRFallidaError
from app.core.providers.ocr_provider import RecognizedWord
from app.core.services.ocr_service import OCRService
from app.core.services.pdf_service import PDFService
from app.core.services.scan_service import ScanService

_LOGGER_NAME = "app.core.services.scan_service"

_SUCCESS_WORD = RecognizedWord(text="Factura", left=100, top=100, width=140, height=40)


class _FakeOCRProviderForScan:
    """Minimal fake `OCRProvider`, own copy per this codebase's "no shared
    test doubles across test files" precedent (mirrors `_deskew.py`/
    `ocr_service.py` own copies of validation logic rather than importing
    each other's private helpers). Used only to keep `OCRService`'s real
    orchestration logic exercised here without a real Tesseract binary.
    """

    def reconocer(self, image: Image.Image) -> list[RecognizedWord]:
        return [_SUCCESS_WORD]

    def esta_disponible(self) -> tuple[bool, str]:
        return True, "fake provider always available"


class FakeDeskewFn:
    """Configurable fake `deskew_fn` for `ScanService` tests.

    `behaviors` maps image filename -> `"succeed"` | `"degrade"` |
    `"raise"`; a filename not present defaults to `"succeed"`. Every
    non-raising call copies `src` to `dst` (so a downstream REAL
    `PDFService.jpg_to_pdf` has a real, valid image to assemble),
    matching `deskew_and_crop`'s own read/write contract without doing
    any real cv2 work.

    Records every `src` it was called with (`calls`) and every `dst`'s
    parent directory it wrote into (`tmp_dirs_seen`) — the latter lets
    tests assert on `ScanService`'s internal `TemporaryDirectory` even
    though it's never exposed directly.

    Optionally appends `"deskew:<filename>"` into a SHARED `call_log`
    list (the same list `FakePdfService`/`FakeOcrService` append into) —
    without this, a composition-order test comparing `deskew.calls` and
    `call_log` as two SEPARATE lists can only prove each list's own
    internal order, not that every deskew call actually preceded
    `jpg_to_pdf`/`ocr` in one real timeline (a bug that moved `jpg_to_pdf`
    inside the deskew loop, called once per image, could still pass a
    two-separate-lists assertion).
    """

    def __init__(
        self, behaviors: dict[str, str] | None = None, call_log: list[str] | None = None
    ) -> None:
        self.behaviors = behaviors or {}
        self.calls: list[Path] = []
        self.tmp_dirs_seen: list[Path] = []
        self._call_log = call_log

    def __call__(self, src: Path, dst: Path) -> bool:
        self.calls.append(src)
        self.tmp_dirs_seen.append(dst.parent)
        if self._call_log is not None:
            self._call_log.append(f"deskew:{src.name}")
        behavior = self.behaviors.get(src.name, "succeed")
        if behavior == "raise":
            raise RuntimeError("fake raw cv2 failure")
        shutil.copyfile(src, dst)
        return behavior != "degrade"


class FakePdfService:
    """Configurable fake `PDFService` for composition/error-injection tests."""

    def __init__(self, call_log: list[str], behavior: str = "succeed") -> None:
        self.call_log = call_log
        self.behavior = behavior
        self.called_with: tuple[list[Path], Path] | None = None

    def jpg_to_pdf(self, images, output: Path) -> Path:
        self.call_log.append("jpg_to_pdf")
        self.called_with = (list(images), output)
        if self.behavior == "raise":
            raise EntradaInvalidaError("fake jpg_to_pdf failure")
        output.write_bytes(b"%PDF-fake-intermediate")
        return output


class FakeOcrService:
    """Configurable fake `OCRService` for composition/error-injection tests."""

    def __init__(self, call_log: list[str], behavior: str = "succeed") -> None:
        self.call_log = call_log
        self.behavior = behavior
        self.called_with: tuple[Path, Path] | None = None

    def ocr(self, source: Path, output: Path) -> Path:
        self.call_log.append("ocr")
        self.called_with = (source, output)
        if self.behavior == "raise":
            raise OCRFallidaError("fake ocr failure")
        output.write_bytes(b"%PDF-fake-output")
        return output


def _real_ocr_service() -> OCRService:
    """Return a REAL `OCRService` backed by a fake provider (no real Tesseract)."""
    return OCRService(provider=_FakeOCRProviderForScan())


class TestValidation:
    def test_empty_images_rejected_before_any_processing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        deskew = FakeDeskewFn()
        pdf_calls: list[str] = []
        pdf_service = FakePdfService(pdf_calls)
        ocr_service = FakeOcrService(pdf_calls)
        service = ScanService(deskew, pdf_service, ocr_service)

        with (
            caplog.at_level(logging.WARNING, logger=_LOGGER_NAME),
            pytest.raises(EntradaInvalidaError),
        ):
            service.scan_to_pdf([], tmp_path / "out.pdf")

        assert deskew.calls == []
        assert pdf_calls == []
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 1

    def test_missing_image_rejects_whole_batch_before_deskew(
        self,
        tmp_path: Path,
        jpg_factory: Callable[..., Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        valid = jpg_factory("page1.jpg")
        missing = tmp_path / "missing.jpg"
        deskew = FakeDeskewFn()
        pdf_calls: list[str] = []
        service = ScanService(deskew, FakePdfService(pdf_calls), FakeOcrService(pdf_calls))

        with (
            caplog.at_level(logging.WARNING, logger=_LOGGER_NAME),
            pytest.raises(EntradaInvalidaError),
        ):
            service.scan_to_pdf([valid, missing], tmp_path / "out.pdf")

        assert deskew.calls == []
        assert pdf_calls == []
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("missing.jpg" in r.getMessage() for r in warning_records)

    def test_empty_image_file_rejects_whole_batch(
        self,
        tmp_path: Path,
        jpg_factory: Callable[..., Path],
        empty_file_factory: Callable[..., Path],
    ) -> None:
        valid = jpg_factory("page1.jpg")
        empty = empty_file_factory("empty.jpg")
        deskew = FakeDeskewFn()
        service = ScanService(deskew, FakePdfService([]), FakeOcrService([]))

        with pytest.raises(EntradaInvalidaError):
            service.scan_to_pdf([valid, empty], tmp_path / "out.pdf")

        assert deskew.calls == []


class TestHappyPathAndComposition:
    def test_successful_call_returns_output_path_and_produces_searchable_pdf(
        self, tmp_path: Path, jpg_factory: Callable[..., Path]
    ) -> None:
        page1 = jpg_factory("page1.jpg")
        page2 = jpg_factory("page2.jpg")
        deskew = FakeDeskewFn()
        service = ScanService(deskew, PDFService(), _real_ocr_service())
        output = tmp_path / "out.pdf"

        result = service.scan_to_pdf([page1, page2], output)

        assert result == output
        assert output.exists()
        doc = pymupdf.open(output)
        try:
            # Two source images -> two assembled pages, each carrying a
            # selectable text layer from `OCRService.ocr`'s overlay. The
            # 64x64 fixture image is too small to fit the word's full
            # bbox without clipping at the page edge (`OCRService`'s own
            # positional-alignment accuracy is already covered by
            # `test_ocr_service.py`), so this only asserts the searchable
            # layer exists, not its exact rendered text.
            assert doc.page_count == 2
            assert doc[0].get_text().strip() != ""
            assert doc[1].get_text().strip() != ""
        finally:
            doc.close()

    def test_deskew_called_once_per_image_before_pdf_service_before_ocr(
        self, tmp_path: Path, jpg_factory: Callable[..., Path]
    ) -> None:
        page1 = jpg_factory("page1.jpg")
        page2 = jpg_factory("page2.jpg")
        call_log: list[str] = []
        deskew = FakeDeskewFn(call_log=call_log)
        pdf_service = FakePdfService(call_log)
        ocr_service = FakeOcrService(call_log)
        service = ScanService(deskew, pdf_service, ocr_service)

        service.scan_to_pdf([page1, page2], tmp_path / "out.pdf")

        assert deskew.calls == [page1, page2]
        # One shared timeline (not two separately-ordered lists): proves
        # every deskew call genuinely precedes jpg_to_pdf/ocr, not just
        # that each fake's own calls happen to be internally ordered.
        assert call_log == ["deskew:page1.jpg", "deskew:page2.jpg", "jpg_to_pdf", "ocr"]
        assert pdf_service.called_with is not None
        assert len(pdf_service.called_with[0]) == 2
        assert ocr_service.called_with is not None
        assert ocr_service.called_with[0] == pdf_service.called_with[1]


class TestPerImageDegradation:
    def test_one_degraded_image_does_not_fail_the_batch(
        self,
        tmp_path: Path,
        jpg_factory: Callable[..., Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        page1 = jpg_factory("page1.jpg")
        page2 = jpg_factory("page2.jpg")
        deskew = FakeDeskewFn(behaviors={"page2.jpg": "degrade"})
        call_log: list[str] = []
        service = ScanService(deskew, FakePdfService(call_log), FakeOcrService(call_log))

        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            result = service.scan_to_pdf([page1, page2], tmp_path / "out.pdf")

        assert result == tmp_path / "out.pdf"
        assert call_log == ["jpg_to_pdf", "ocr"]
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("page2.jpg" in r.getMessage() for r in warning_records)


class TestExceptionContainment:
    def test_raw_deskew_exception_is_translated_not_raw(
        self, tmp_path: Path, jpg_factory: Callable[..., Path]
    ) -> None:
        page1 = jpg_factory("page1.jpg")
        deskew = FakeDeskewFn(behaviors={"page1.jpg": "raise"})
        service = ScanService(deskew, FakePdfService([]), FakeOcrService([]))

        with pytest.raises(EntradaInvalidaError) as exc_info:
            service.scan_to_pdf([page1], tmp_path / "out.pdf")

        assert type(exc_info.value) is EntradaInvalidaError

    def test_raw_deskew_exception_logs_warning_filename_only(
        self,
        tmp_path: Path,
        jpg_factory: Callable[..., Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        page1 = jpg_factory("page1.jpg")
        deskew = FakeDeskewFn(behaviors={"page1.jpg": "raise"})
        service = ScanService(deskew, FakePdfService([]), FakeOcrService([]))

        with (
            caplog.at_level(logging.WARNING, logger=_LOGGER_NAME),
            pytest.raises(EntradaInvalidaError),
        ):
            service.scan_to_pdf([page1], tmp_path / "out.pdf")

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("page1.jpg" in r.getMessage() for r in warning_records)
        for record in warning_records:
            assert str(tmp_path) not in record.getMessage()

    def test_jpg_to_pdf_domain_exception_propagates_unwrapped(
        self, tmp_path: Path, jpg_factory: Callable[..., Path]
    ) -> None:
        page1 = jpg_factory("page1.jpg")
        deskew = FakeDeskewFn()
        call_log: list[str] = []
        pdf_service = FakePdfService(call_log, behavior="raise")
        ocr_service = FakeOcrService(call_log)
        service = ScanService(deskew, pdf_service, ocr_service)

        with pytest.raises(EntradaInvalidaError) as exc_info:
            service.scan_to_pdf([page1], tmp_path / "out.pdf")

        assert type(exc_info.value) is EntradaInvalidaError
        assert call_log == ["jpg_to_pdf"]

    def test_ocr_domain_exception_propagates_unwrapped(
        self, tmp_path: Path, jpg_factory: Callable[..., Path]
    ) -> None:
        page1 = jpg_factory("page1.jpg")
        deskew = FakeDeskewFn()
        call_log: list[str] = []
        pdf_service = FakePdfService(call_log)
        ocr_service = FakeOcrService(call_log, behavior="raise")
        service = ScanService(deskew, pdf_service, ocr_service)

        with pytest.raises(OCRFallidaError) as exc_info:
            service.scan_to_pdf([page1], tmp_path / "out.pdf")

        assert type(exc_info.value) is OCRFallidaError
        assert call_log == ["jpg_to_pdf", "ocr"]

    def test_temp_directory_os_error_is_translated_not_raw(
        self,
        tmp_path: Path,
        jpg_factory: Callable[..., Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression: `tempfile.TemporaryDirectory()` is raw OS I/O with
        no translation of its own — `_translate_deskew_errors` only
        covers `deskew_fn`. A restricted `%TEMP%`/disk-full/AV-locked
        cleanup must still surface as `EntradaInvalidaError`, never a
        raw `OSError`."""
        page1 = jpg_factory("page1.jpg")
        service = ScanService(FakeDeskewFn(), FakePdfService([]), FakeOcrService([]))

        def _raise_os_error(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated restricted %TEMP%")

        monkeypatch.setattr(
            "app.core.services.scan_service.tempfile.TemporaryDirectory", _raise_os_error
        )

        with pytest.raises(EntradaInvalidaError) as exc_info:
            service.scan_to_pdf([page1], tmp_path / "out.pdf")

        assert type(exc_info.value) is EntradaInvalidaError


class TestTempCleanup:
    def test_no_leftover_temp_files_after_success(
        self, tmp_path: Path, jpg_factory: Callable[..., Path]
    ) -> None:
        page1 = jpg_factory("page1.jpg")
        deskew = FakeDeskewFn()
        call_log: list[str] = []
        service = ScanService(deskew, FakePdfService(call_log), FakeOcrService(call_log))

        service.scan_to_pdf([page1], tmp_path / "out.pdf")

        assert len(deskew.tmp_dirs_seen) == 1
        assert not deskew.tmp_dirs_seen[0].exists()

    def test_no_leftover_temp_files_after_failure_partway_through(
        self, tmp_path: Path, jpg_factory: Callable[..., Path]
    ) -> None:
        page1 = jpg_factory("page1.jpg")
        deskew = FakeDeskewFn()
        call_log: list[str] = []
        pdf_service = FakePdfService(call_log, behavior="raise")
        service = ScanService(deskew, pdf_service, FakeOcrService(call_log))

        with pytest.raises(EntradaInvalidaError):
            service.scan_to_pdf([page1], tmp_path / "out.pdf")

        assert len(deskew.tmp_dirs_seen) == 1
        assert not deskew.tmp_dirs_seen[0].exists()

    def test_no_leftover_temp_files_after_deskew_raises(
        self, tmp_path: Path, jpg_factory: Callable[..., Path]
    ) -> None:
        page1 = jpg_factory("page1.jpg")
        deskew = FakeDeskewFn(behaviors={"page1.jpg": "raise"})
        call_log: list[str] = []
        service = ScanService(deskew, FakePdfService(call_log), FakeOcrService(call_log))

        with pytest.raises(EntradaInvalidaError):
            service.scan_to_pdf([page1], tmp_path / "out.pdf")

        assert len(deskew.tmp_dirs_seen) == 1
        assert not deskew.tmp_dirs_seen[0].exists()


class TestLogging:
    def test_success_logs_one_info_start_and_one_info_ok_filename_only(
        self,
        tmp_path: Path,
        jpg_factory: Callable[..., Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        page1 = jpg_factory("page1.jpg")
        deskew = FakeDeskewFn()
        call_log: list[str] = []
        service = ScanService(deskew, FakePdfService(call_log), FakeOcrService(call_log))

        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            service.scan_to_pdf([page1], tmp_path / "out.pdf")

        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("start" in r.getMessage() for r in info_records)
        assert any("ok" in r.getMessage() for r in info_records)
        for record in info_records:
            assert str(tmp_path) not in record.getMessage()

    def test_start_log_precedes_validation_failure(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        deskew = FakeDeskewFn()
        service = ScanService(deskew, FakePdfService([]), FakeOcrService([]))

        with (
            caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
            pytest.raises(EntradaInvalidaError),
        ):
            service.scan_to_pdf([], tmp_path / "out.pdf")

        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("start" in r.getMessage() for r in info_records)


def test_default_construction_uses_real_deskew_and_composed_services() -> None:
    service = ScanService()

    assert callable(service._deskew_fn)
    assert isinstance(service._pdf_service, PDFService)
    assert isinstance(service._ocr_service, OCRService)
