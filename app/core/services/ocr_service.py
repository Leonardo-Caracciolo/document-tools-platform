"""`OCRService` — image-only PDF -> searchable PDF orchestrator, per SSD §6.1.

`OCRService` is NOT a `PDFService` extension, mirroring
`sdd/word-to-pdf-provider/design`'s "Technical Approach" precedent for
`ExportService`: it owns its own validation and error-translation
boundary rather than importing `PDFService`'s or `ExportService`'s
private helpers. Providers (`TesseractOCRProvider`, `AzureOCRProvider`)
own only the raw recognition call; `OCRService` is the sole caller
responsible for translating their failures to the domain exceptions in
`app.core.exceptions`, rasterizing each page to an in-memory image, and
overlaying an invisible, positionally aligned text layer per recognized
word (per `sdd/ocr-pdf-provider/design`'s empirically confirmed
Decision 2 transform).

Provider selection is factory-by-config with an optional injection
override, identical in shape to `ExportService`'s `DOC_CONVERTER_PROVIDER`
pattern (see `app.core.services.export_service`'s docstring): when
`provider` is omitted, an `OCR_PROVIDER` env var read exactly once at
construction picks the concrete provider class from `_PROVIDERS`; an
explicit `provider` argument overrides the factory entirely, which is
what makes fake-provider unit testing possible without a real Tesseract
installation.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pymupdf
from PIL import Image

from app.core.exceptions import (
    EntradaInvalidaError,
    OCRFallidaError,
    OCRNoDisponibleError,
)
from app.core.providers.azure_ocr_provider import AzureOCRProvider
from app.core.providers.ocr_provider import OCRProvider
from app.core.providers.tesseract_ocr_provider import TesseractOCRProvider
from app.infrastructure.logger import get_logger

#: `OCR_PROVIDER` value -> concrete provider class. Mirrors
#: `app.core.services.export_service._PROVIDERS`'s exact shape/rationale
#: (see that module's docstring) — `OCRService` reads the env var
#: directly, not via `AppConfig.load()`, for the same reason `ExportService`
#: does: routing selection through `AppConfig.load()` would force every
#: `OCRService()` construction to also satisfy unrelated required config.
_PROVIDERS: dict[str, type[OCRProvider]] = {
    "tesseract": TesseractOCRProvider,
    "azure_di": AzureOCRProvider,
}

#: Provider key used when `OCR_PROVIDER` is unset or names a provider not
#: in `_PROVIDERS`.
_DEFAULT_PROVIDER_KEY = "tesseract"

#: Rasterization DPI for each page before recognition, and the reference
#: DPI the pixel-space -> PDF-point-space overlay transform below assumes.
#: Matches the DPI design's empirical alignment check was run and
#: confirmed accurate to <1pt at.
_RASTER_DPI = 300

#: PDF points per inch — the fixed constant behind the
#: pixel-space -> point-space transform (`value * _POINTS_PER_INCH /
#: _RASTER_DPI`), confirmed accurate to <1pt during design (see
#: `sdd/ocr-pdf-provider/design`, "Empirical status" (b)).
_POINTS_PER_INCH = 72


class OCRService:
    """Orchestrates image-only-PDF -> searchable-PDF OCR behind `OCRProvider`.

    Holds a logger and the selected/injected provider — no other state.
    """

    def __init__(self, provider: OCRProvider | None = None) -> None:
        self._log = get_logger(__name__)
        if provider is not None:
            self._provider: OCRProvider = provider
        else:
            # Read exactly once, at construction — per spec's "Provider
            # Selection" requirement, a later change to the env var must
            # NOT affect an already-constructed instance.
            key = os.environ.get("OCR_PROVIDER", _DEFAULT_PROVIDER_KEY)
            provider_cls = _PROVIDERS.get(key, _PROVIDERS[_DEFAULT_PROVIDER_KEY])
            self._provider = provider_cls()

    @contextmanager
    def _translate_provider_errors(self, source: Path) -> Generator[None, None, None]:
        """Map recognition/pymupdf exceptions raised while processing `source`.

        Scoped to wrap the ENTIRE open-rasterize-recognize-overlay-save
        pipeline in `ocr()` — NOT just `provider.reconocer()`. Unlike
        `ExportService` (which never calls `pymupdf` itself, only its
        provider), `OCRService` calls `pymupdf.open`/`page.get_pixmap`/
        `page.insert_text`/`doc.save` directly, the same way `PDFService`
        does — and `PDFService` has its own dedicated
        `_translate_pymupdf_errors` boundary specifically because
        `pymupdf.open` raises a raw `FileDataError`/`EmptyFileError` on a
        structurally corrupt (but non-empty, so `_require_nonempty_pdf`
        doesn't catch it) input. An earlier revision of this method wrapped
        only `provider.reconocer()`, mirroring `ExportService` too
        narrowly — review caught that a corrupt `.pdf` let a raw
        `pymupdf.FileDataError` (with the full absolute source path in its
        message) escape uncaught. Validation (`EntradaInvalidaError`)
        still runs OUTSIDE this context manager and propagates unwrapped.

        Mapping:
            `OCRNoDisponibleError`, `OCRFallidaError`, `NotImplementedError`
                -> re-raised unwrapped (the domain pair plus the Azure
                stub's intentional v1 behavior)
            any other exception (raw `pymupdf.FileDataError`/`EmptyFileError`
                on open/save, bare `RuntimeError` incl. "Tesseract process
                timeout", `TimeoutError`, any other raw `pytesseract`/
                Tesseract failure) -> `OCRFallidaError`, filename-only
        """
        try:
            yield
        except (OCRNoDisponibleError, OCRFallidaError, NotImplementedError):
            raise
        except Exception as exc:
            self._log.warning("ocr failed: recognition error (%s)", source.name)
            raise OCRFallidaError(f"OCR recognition of {source.name!r} failed.") from exc

    def _require_nonempty_pdf(self, source: Path) -> None:
        """Raise `EntradaInvalidaError` if `source` is not a non-empty `.pdf` file.

        Own copy — NOT imported from `PDFService`/`ExportService` (design
        confirms `OCRService` is not a `PDFService` extension, mirroring
        `ExportService`'s own precedent). Extension is checked first so a
        wrong-extension file never reaches the filesystem stat call.
        """
        if source.suffix.lower() != ".pdf":
            self._log.warning("ocr failed: not a .pdf file (%s)", source.name)
            raise EntradaInvalidaError(f"{source.name!r} is not a PDF file.")
        if not source.is_file() or source.stat().st_size == 0:
            self._log.warning("ocr failed: missing or empty input (%s)", source.name)
            raise EntradaInvalidaError(f"{source.name!r} is empty or does not exist.")

    def _make_output_dir(self, directory: Path) -> None:
        """Create `directory` (and parents) if needed, or raise `EntradaInvalidaError`.

        Own copy — NOT imported from `PDFService`/`ExportService`. Runs
        BEFORE the provider is ever invoked, same as
        `ExportService._make_output_dir`.
        """
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._log.warning(
                "ocr failed: cannot create output directory (%s)", directory.name
            )
            raise EntradaInvalidaError(
                f"Cannot create output directory {directory.name!r}."
            ) from exc

    def ocr(self, source: Path, output: Path) -> Path:
        """Recognize text in `source` and write a searchable PDF to `output`.

        Rasterizes each page of `source` to an in-memory `PIL.Image` at
        `_RASTER_DPI`, recognizes text via the active provider, and
        overlays each recognized word as invisible, selectable text
        (`render_mode=3`) at its transformed position — the empirically
        confirmed pixel-space -> point-space mapping from design:
        `point = (left*72/300, (top+height)*72/300)`,
        `fontsize = height*72/300`.

        `output` is written only after every page has been recognized and
        overlaid successfully (validate-then-write, same invariant as
        `PDFService`/`ExportService`): a mid-recognition provider failure
        on any page leaves no output file behind.

        Raises:
            EntradaInvalidaError: `source` is not a `.pdf`, is missing/
                empty, or `output`'s parent directory cannot be created.
                Raised before the provider is ever invoked.
            OCRNoDisponibleError: the active provider reports it cannot
                perform recognition right now.
            OCRFallidaError: the provider's recognition failed or
                exceeded its timeout on any page, OR `source` is
                structurally corrupt/unrasterizable (non-empty but not a
                valid PDF — `_require_nonempty_pdf` only checks extension
                and size, not parseability) or `output` failed to save.
            NotImplementedError: the Azure stub provider is active — the
                intentional v1 deliverable for that provider, not
                translated to a domain exception.
        """
        self._log.info("ocr start: %s", source.name)

        self._require_nonempty_pdf(source)
        self._make_output_dir(output.parent)

        with self._translate_provider_errors(source):
            doc = pymupdf.open(source)
            try:
                for page in doc:
                    pix = page.get_pixmap(dpi=_RASTER_DPI)
                    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

                    words = self._provider.reconocer(image)

                    for word in words:
                        point = (
                            word.left * _POINTS_PER_INCH / _RASTER_DPI,
                            (word.top + word.height) * _POINTS_PER_INCH / _RASTER_DPI,
                        )
                        fontsize = word.height * _POINTS_PER_INCH / _RASTER_DPI
                        page.insert_text(point, word.text, fontsize=fontsize, render_mode=3)

                # All pages recognized and overlaid: safe to write now.
                doc.save(output)
            finally:
                doc.close()

        self._log.info("ocr ok: %s -> %s", source.name, output.name)
        return output
