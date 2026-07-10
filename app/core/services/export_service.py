"""`ExportService` — `.docx` <-> PDF conversion orchestrator, per SSD §5.1/§6.2/§4.1.

`ExportService` is NOT a `PDFService` extension (see
`sdd/word-to-pdf-provider/design`, "Technical Approach"): it owns its own
validation and error-translation boundary, mirroring `PDFService`'s
scoped-boundary + validate-then-write conventions without importing from
it or sharing its private helpers. Providers (`ComWordProvider`,
`AzureDocConverterProvider`) own only the raw engine call; `ExportService`
is the sole caller responsible for translating their failures to the
domain exceptions in `app.core.exceptions`.

Provider selection is factory-by-config with an optional injection
override (design Decision 2): when `provider` is omitted, a
`DOC_CONVERTER_PROVIDER` env var read exactly once at construction picks
the concrete provider class from `_PROVIDERS`; an explicit `provider`
argument overrides the factory entirely, which is what makes fake-provider
unit testing possible without real Office/COM.

`pdf_a_word` (`sdd/pdf-to-word/design`) is the reverse direction, added
later and additive: it calls `pdf2docx` directly (no provider
abstraction exists for it, no cloud/local split), the same way
`PDFService` calls `pikepdf`/`pymupdf` directly. It does NOT participate
in `_PROVIDERS`/the `convertir` provider factory, and `convertir`'s own
behavior is untouched by it.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pymupdf
from pdf2docx import Converter

from app.core.exceptions import (
    ConversionFallidaError,
    ConversorNoDisponibleError,
    EntradaInvalidaError,
    PDFCorruptoError,
    PDFSinTextoError,
)
from app.core.providers.azure_doc_converter_provider import AzureDocConverterProvider
from app.core.providers.com_word_provider import ComWordProvider
from app.core.providers.document_converter_provider import DocumentConverterProvider
from app.infrastructure.logger import get_logger

#: Minimum non-whitespace character count across all pages of a `.pdf`
#: `pdf_a_word` will accept as "has native text". Confirmed during
#: `sdd/pdf-to-word/design`'s empirical pass: `pdf2docx` does NOT raise on
#: a scanned/image-only PDF — it silently produces a garbage, near-empty
#: `.docx` and reports success. This threshold is the ONLY gate that
#: correctly rejects that input before `pdf2docx` ever touches it.
#: Synthetic fixtures give a clean 0-vs-hundreds separation, so any value
#: in [1, hundreds) would pass tests; 16 is a v1 starting value to also
#: guard real-world near-empty stray-artifact text layers, same tunable
#: status as `PDFService.compress`'s recompression constants.
_MIN_TEXT_CHARS = 16

#: `DOC_CONVERTER_PROVIDER` value -> concrete provider class. Mirrors the
#: same env var `app.infrastructure.config.DocConverterConfig` reads, but
#: `ExportService` reads it directly (not via `AppConfig.load()`):
#: `PDFService` takes no config injection either (see its own docstring),
#: and routing provider selection through `AppConfig.load()` would force
#: every `ExportService()` construction to also satisfy `AppConfig`'s
#: unrelated required `LOG_LEVEL` key. Generalizes verbatim to a future
#: `OCRService`/`OCR_PROVIDER` (SSD §6.1).
_PROVIDERS: dict[str, type[DocumentConverterProvider]] = {
    "com": ComWordProvider,
    "azure": AzureDocConverterProvider,
}

#: Provider key used when `DOC_CONVERTER_PROVIDER` is unset or names a
#: provider not in `_PROVIDERS`.
_DEFAULT_PROVIDER_KEY = "com"


class ExportService:
    """Orchestrates a single `.docx` -> PDF conversion behind `DocumentConverterProvider`.

    Holds a logger and the selected/injected provider — no other state.
    """

    def __init__(self, provider: DocumentConverterProvider | None = None) -> None:
        self._log = get_logger(__name__)
        if provider is not None:
            self._provider: DocumentConverterProvider = provider
        else:
            # Read exactly once, at construction — per spec's "Provider
            # Selection" requirement, a later change to the env var must
            # NOT affect an already-constructed instance.
            key = os.environ.get("DOC_CONVERTER_PROVIDER", _DEFAULT_PROVIDER_KEY)
            provider_cls = _PROVIDERS.get(key, _PROVIDERS[_DEFAULT_PROVIDER_KEY])
            self._provider = provider_cls()

    @contextmanager
    def _translate_provider_errors(self, source: Path) -> Generator[None, None, None]:
        """Map provider exceptions raised while converting `source`.

        Scoped to wrap ONLY the `provider.convertir()` call — validation
        (`EntradaInvalidaError`) runs outside this context manager and
        propagates unwrapped, same convention as `PDFService._translate_errors`.

        Mapping:
            `ConversorNoDisponibleError`, `ConversionFallidaError`,
                `NotImplementedError` -> re-raised unwrapped (the domain
                trio plus the Azure stub's intentional v1 behavior)
            any other exception (`TimeoutError`, raw COM/`pywin32`
                `RuntimeError`, etc.) -> `ConversionFallidaError`
        """
        try:
            yield
        except (ConversorNoDisponibleError, ConversionFallidaError, NotImplementedError):
            raise
        except Exception as exc:
            self._log.warning("export failed: conversion error (%s)", source.name)
            raise ConversionFallidaError(
                f"Conversion of {source.name!r} failed."
            ) from exc

    def _require_nonempty_docx(self, source: Path) -> None:
        """Raise `EntradaInvalidaError` if `source` is not a non-empty `.docx` file.

        Own copy — NOT imported from `PDFService` (design confirms
        `ExportService` is not a `PDFService` extension). Extension is
        checked first so a wrong-extension file never reaches the
        filesystem stat call.
        """
        if source.suffix.lower() != ".docx":
            self._log.warning("export failed: not a .docx file (%s)", source.name)
            raise EntradaInvalidaError(f"{source.name!r} is not a .docx file.")
        if not source.is_file() or source.stat().st_size == 0:
            self._log.warning("export failed: missing or empty input (%s)", source.name)
            raise EntradaInvalidaError(f"{source.name!r} is empty or does not exist.")

    def _make_output_dir(self, directory: Path) -> None:
        """Create `directory` (and parents) if needed, or raise `EntradaInvalidaError`.

        Own copy — NOT imported from `PDFService`. Per design's Data Flow,
        this runs BEFORE the provider is ever invoked (unlike some of
        `PDFService`'s operations, which validate-then-write only after a
        library call already succeeded) — a document conversion has no
        equivalent "already opened and validated" midpoint to defer to.
        """
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._log.warning(
                "export failed: cannot create output directory (%s)", directory.name
            )
            raise EntradaInvalidaError(
                f"Cannot create output directory {directory.name!r}."
            ) from exc

    def convertir(self, source: Path, output: Path) -> Path:
        """Convert `source` (`.docx`) to a PDF at `output` via the active provider.

        Raises:
            EntradaInvalidaError: `source` is not a `.docx`, is missing/
                empty, or `output`'s parent directory cannot be created.
                Raised before the provider is ever invoked.
            ConversorNoDisponibleError: the active provider reports it
                cannot perform the conversion right now.
            ConversionFallidaError: the provider's conversion failed or
                exceeded its timeout.
            NotImplementedError: the Azure stub provider is active — the
                intentional v1 deliverable for that provider, not
                translated to a domain exception.
        """
        self._log.info("export start: %s", source.name)

        self._require_nonempty_docx(source)
        self._make_output_dir(output.parent)

        with self._translate_provider_errors(source):
            self._provider.convertir(source, output)

        self._log.info("export ok: %s -> %s", source.name, output.name)
        return output

    def _require_nonempty_pdf(self, source: Path) -> None:
        """Raise `EntradaInvalidaError` if `source` is not a non-empty `.pdf` file.

        Own copy — NOT imported from `PDFService`/`OCRService` (same
        precedent as `_require_nonempty_docx` above and
        `OCRService._require_nonempty_pdf`). Extension is checked first
        so a wrong-extension file never reaches the filesystem stat call.
        """
        if source.suffix.lower() != ".pdf":
            self._log.warning("export failed: not a .pdf file (%s)", source.name)
            raise EntradaInvalidaError(f"{source.name!r} is not a PDF file.")
        if not source.is_file() or source.stat().st_size == 0:
            self._log.warning("export failed: missing or empty input (%s)", source.name)
            raise EntradaInvalidaError(f"{source.name!r} is empty or does not exist.")

    @contextmanager
    def _translate_pdf_open_errors(self, source: Path) -> Generator[None, None, None]:
        """Map raw `pymupdf` exceptions raised while opening/parsing `source`.

        Scoped to wrap BOTH the `pymupdf.open`/`get_text()` calls used for
        scanned-PDF detection AND the `pdf2docx.Converter(...)`
        CONSTRUCTOR call — confirmed during design's empirical pass that
        `Converter(corrupt_path)` itself raises the identical raw
        `pymupdf.FileDataError` a corrupt PDF raises on `pymupdf.open`,
        not just `.convert()`. A boundary that only wrapped `.convert()`
        would let that constructor-time exception escape uncaught.

        Mapping: any exception -> `PDFCorruptoError`. Deliberately a broad
        `except Exception`, not narrowed to `pymupdf.FileDataError`/
        `EmptyFileError` only: this boundary spans `page.get_text()`
        across every page of an arbitrary user-supplied PDF, a much
        larger and more heterogeneous surface than a single controlled
        library call. `OCRService`'s own boundary (`ocr_service.py`) was
        widened to `except Exception` for the identical reason, after a
        narrower version let a raw exception escape uncaught on a real
        input — don't repeat that gap here on a first pass.
        """
        try:
            yield
        except Exception as exc:
            self._log.warning("export failed: corrupt PDF (%s)", source.name)
            raise PDFCorruptoError(f"{source.name!r} is corrupt or unreadable.") from exc

    @contextmanager
    def _translate_conversion_errors(self, source: Path) -> Generator[None, None, None]:
        """Map raw `pdf2docx` exceptions raised while converting `source`.

        Scoped to wrap ONLY the `Converter.convert()` call. Deliberately
        does NOT touch `output` itself — `pdf_a_word` converts into a
        TEMP file first and only moves it to `output` on success (see
        that method's docstring), so there is nothing at the real
        `output` path for this boundary to clean up. An earlier revision
        called `output.unlink(missing_ok=True)` here directly, which had
        a real data-loss bug: if `output` already pointed at a
        pre-existing file (e.g. the caller overwriting a prior
        successful conversion), a failed `.convert()` would leave that
        file partially overwritten by `pdf2docx`, and this cleanup would
        then DELETE it outright — destroying the caller's original file
        even though nothing succeeded. Converting into a temp path first
        removes that risk entirely: `output` is never touched until
        conversion is fully done.

        Mapping:
            any exception -> `ConversionFallidaError`, filename-only warning
        """
        try:
            yield
        except Exception as exc:
            self._log.warning("export failed: conversion error (%s)", source.name)
            raise ConversionFallidaError(f"Conversion of {source.name!r} failed.") from exc

    def pdf_a_word(self, source: Path, output: Path) -> Path:
        """Convert `source` (`.pdf`) to a `.docx` at `output` via `pdf2docx`.

        Detects, BEFORE attempting conversion, whether `source` has any
        extractable text (accumulated non-whitespace `get_text()` chars
        across all pages, threshold `_MIN_TEXT_CHARS`). Confirmed during
        design's empirical pass that `pdf2docx` does NOT raise on a
        scanned/image-only PDF — it silently produces a garbage,
        near-empty `.docx` and reports success — so this upfront gate is
        the only correct way to reject that input; detection never
        depends on `pdf2docx`'s own behavior.

        Converts into a TEMP file first, then moves it to `output` only
        on success (true validate-then-write): `pdf2docx.Converter` has
        no separate "already validated, ready to write" midpoint the way
        `pikepdf`/`pymupdf`-based operations do, and unlinking `output`
        directly on failure would risk deleting a pre-existing file at
        that path that had nothing to do with this call (see
        `_translate_conversion_errors`'s docstring). Converting into a
        temp path removes that risk: `output` is never touched until the
        conversion is fully done.

        Raises:
            EntradaInvalidaError: `source` is not a `.pdf`, is missing/
                empty, or `output`'s parent directory cannot be created.
                Raised before any pymupdf/pdf2docx call.
            PDFCorruptoError: `source` cannot be opened/parsed by
                `pymupdf`, or `pdf2docx.Converter(...)` fails to
                construct on it (same underlying raw exception).
            PDFSinTextoError: `source` is a valid, parseable PDF with no
                extractable text on any page (likely scanned/image-only).
                Message suggests running OCR PDF first. `pdf2docx` is
                never invoked for this input.
            ConversionFallidaError: `pdf2docx` fails to convert an
                otherwise-valid, native-text `source`. `output` is left
                untouched — including if it already existed before this
                call.
        """
        self._log.info("export start: %s", source.name)

        self._require_nonempty_pdf(source)
        self._make_output_dir(output.parent)

        with self._translate_pdf_open_errors(source):
            doc = pymupdf.open(source)
            try:
                text_chars = sum(len("".join(page.get_text().split())) for page in doc)
            finally:
                doc.close()

        if text_chars < _MIN_TEXT_CHARS:
            self._log.warning("export failed: no extractable text (%s)", source.name)
            raise PDFSinTextoError(
                f"{source.name!r} has no extractable text; run OCR PDF first."
            )

        with self._translate_pdf_open_errors(source):
            cv = Converter(str(source))

        try:
            try:
                fd, tmp_name = tempfile.mkstemp(suffix=".docx", dir=output.parent)
                os.close(fd)
            except OSError as exc:
                self._log.warning(
                    "export failed: cannot create temp file (%s)", source.name
                )
                raise EntradaInvalidaError(
                    f"Cannot create a temporary file near {output.name!r}."
                ) from exc

            tmp_path = Path(tmp_name)
            try:
                with self._translate_conversion_errors(source):
                    cv.convert(str(tmp_path))
                try:
                    os.replace(tmp_path, output)
                except OSError as exc:
                    self._log.warning(
                        "export failed: cannot finalize output (%s)", source.name
                    )
                    raise ConversionFallidaError(
                        f"Conversion of {source.name!r} failed."
                    ) from exc
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
        finally:
            cv.close()

        self._log.info("export ok: %s -> %s", source.name, output.name)
        return output
