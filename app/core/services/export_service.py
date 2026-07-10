"""`ExportService` — `.docx` -> PDF conversion orchestrator, per SSD §5.1/§6.2.

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
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from app.core.exceptions import (
    ConversionFallidaError,
    ConversorNoDisponibleError,
    EntradaInvalidaError,
)
from app.core.providers.azure_doc_converter_provider import AzureDocConverterProvider
from app.core.providers.com_word_provider import ComWordProvider
from app.core.providers.document_converter_provider import DocumentConverterProvider
from app.infrastructure.logger import get_logger

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
