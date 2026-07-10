"""`ScanService` — photographed pages -> deskewed/cropped searchable PDF, per SSD §4.1 row 8.

`ScanService` is the FIRST cross-service composition in this codebase.
It owns its own OpenCV deskew/crop step directly (via the injectable
`deskew_fn`, defaulting to `app.core.services._deskew.deskew_and_crop`),
then orchestrates `PDFService.jpg_to_pdf` (assemble the cleaned images
into an intermediate PDF) and `OCRService.ocr` (add a searchable Spanish
text layer to that intermediate PDF, writing the final `output`) through
their PUBLIC method contracts only — it does not depend on either
service's private helpers, per `sdd/scan-to-pdf/design`'s "Technical
Approach".

Mirrors `PDFService`/`OCRService`/`ExportService`'s established
conventions: a scoped error-translation boundary (`_translate_deskew_
errors`, wrapping ONLY the per-image `deskew_fn` call — design's
Decision 2 deliberately does NOT re-wrap the `jpg_to_pdf`/`ocr` calls,
since both already fully translate their own libraries and re-wrapping
risks mangling an already-correct domain exception type into the wrong
one), validate-then-write (every image is validated all-or-nothing
before any temp artifact or `cv2` work, design's Decision 3), and
filename-only logging.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path

from app.core.exceptions import EntradaInvalidaError
from app.core.services._deskew import deskew_and_crop
from app.core.services.ocr_service import OCRService
from app.core.services.pdf_service import PDFService
from app.infrastructure.logger import get_logger


class ScanService:
    """Orchestrates deskew/crop + PDF assembly + OCR for photographed page batches.

    Holds a logger, the injectable deskew callable, and the two composed
    services — no other state. `deskew_fn`/`pdf_service`/`ocr_service`
    are constructor-injectable, mirroring `OCRService`'s optional-
    provider injection (design's Decision 1): this is what makes
    orchestration unit-testable with fakes, without a real cv2/pikepdf/
    Tesseract call.
    """

    def __init__(
        self,
        deskew_fn: Callable[[Path, Path], bool] | None = None,
        pdf_service: PDFService | None = None,
        ocr_service: OCRService | None = None,
    ) -> None:
        self._log = get_logger(__name__)
        self._deskew_fn = deskew_fn if deskew_fn is not None else deskew_and_crop
        self._pdf_service = pdf_service if pdf_service is not None else PDFService()
        self._ocr_service = ocr_service if ocr_service is not None else OCRService()

    @contextmanager
    def _translate_deskew_errors(self, source: Path) -> Generator[None, None, None]:
        """Map a raw exception raised by `self._deskew_fn` while processing `source`.

        Scoped to wrap ONLY the per-image `self._deskew_fn` call — NOT
        the whole pipeline, and deliberately NOT the `jpg_to_pdf`/`ocr`
        calls (design's Decision 2): those already fully translate their
        own libraries at their own boundaries, so wrapping them here
        would risk re-labeling an already-correct domain exception (e.g.
        turning an `OCRFallidaError` into `EntradaInvalidaError`). `cv2`
        is the only raw library `ScanService` calls directly, so it gets
        exactly one scoped boundary, same convention as `PDFService`'s
        `_translate_errors`/`_translate_pymupdf_errors` and `OCRService`'s
        `_translate_provider_errors`.

        A `deskew_fn` returning `False` is NOT an error — that is the
        best-effort degradation signal, handled by the caller outside
        this context manager. Only a raised exception is translated here.
        """
        try:
            yield
        except Exception as exc:
            self._log.warning("scan_to_pdf failed: deskew error (%s)", source.name)
            raise EntradaInvalidaError(f"{source.name!r} could not be processed.") from exc

    def _validate_images(self, images: Sequence[Path]) -> None:
        """Raise `EntradaInvalidaError` if `images` is empty or any entry is missing/0 bytes.

        Own copy — NOT imported from `PDFService`/`OCRService` (mirrors
        their own "no shared private helpers" precedent). All-or-nothing,
        BEFORE any temp artifact or `cv2` work, matching `jpg_to_pdf`'s
        existing all-or-nothing precedent (design's Decision 3).
        """
        if not images:
            self._log.warning("scan_to_pdf failed: no input images provided")
            raise EntradaInvalidaError("No input images provided for scan_to_pdf.")

        for image in images:
            if not image.is_file() or image.stat().st_size == 0:
                self._log.warning(
                    "scan_to_pdf failed: missing or empty input (%s)", image.name
                )
                raise EntradaInvalidaError(f"{image.name!r} is empty or does not exist.")

    def scan_to_pdf(self, images: Sequence[Path], output: Path) -> Path:
        """Deskew/crop each of `images`, assemble them into a PDF, then OCR it to `output`.

        Every image is validated all-or-nothing before any processing
        begins. Cleaned (deskewed/cropped) images and the intermediate
        pre-OCR PDF live entirely inside a `tempfile.TemporaryDirectory`,
        which guarantees cleanup on both success and any exception via
        its own context-manager semantics — no manual cleanup code is
        needed beyond using the `with` block correctly.

        A per-image failure to confidently locate a document boundary
        (`self._deskew_fn` returning `False`) is best-effort degradation,
        not a failure: that image's page is used un-cropped in the final
        output, and the degradation is logged at WARNING with the
        filename only — the batch is never aborted for this condition.

        `PDFService.jpg_to_pdf` and `OCRService.ocr` are called through
        their public contracts only; their own domain exceptions
        propagate unwrapped (design's Decision 2) — `ScanService` does
        not re-translate them.

        Raises:
            EntradaInvalidaError: `images` is empty, any image is
                missing/0 bytes, or `self._deskew_fn` raises a raw
                exception while processing an otherwise-valid image.
            (whatever `PDFService.jpg_to_pdf` raises): propagated
                unwrapped from the intermediate-assembly step.
            (whatever `OCRService.ocr` raises, including
                `NotImplementedError` from the Azure OCR stub): propagated
                unwrapped from the final OCR step.
        """
        self._log.info("scan_to_pdf start: %d image(s)", len(images))

        self._validate_images(images)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cleaned_paths: list[Path] = []
            for index, image in enumerate(images):
                cleaned_path = tmp_path / f"clean_{index}{image.suffix}"
                with self._translate_deskew_errors(image):
                    confident = self._deskew_fn(image, cleaned_path)
                if not confident:
                    self._log.warning(
                        "scan_to_pdf: no confident document boundary, using degraded"
                        " image (%s)",
                        image.name,
                    )
                cleaned_paths.append(cleaned_path)

            intermediate_path = tmp_path / "intermediate.pdf"
            self._pdf_service.jpg_to_pdf(cleaned_paths, intermediate_path)
            self._ocr_service.ocr(intermediate_path, output)

        self._log.info("scan_to_pdf ok: %d image(s) -> %s", len(images), output.name)
        return output
