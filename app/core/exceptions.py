"""Domain exceptions shared across the application.

`ConfigError` is raised by `app.infrastructure.config` when required
configuration is missing or invalid, so the application fails fast at
startup instead of continuing with an undefined value.

`PDFCorruptoError`, `ArchivoProtegidoError`, `ContrasenaInvalidaError`, and
`EntradaInvalidaError` are raised by `app.core.services.pdf_service` so
`pikepdf`/`img2pdf`/`Pillow` failures never reach callers as raw
tracebacks.

`ConversorNoDisponibleError` and `ConversionFallidaError` are raised by
`app.core.services.export_service` so COM/`pywin32` failures never reach
callers as raw tracebacks. `PDFSinTextoError` is also raised by
`app.core.services.export_service`, alongside `PDFCorruptoError` and
`ConversionFallidaError`, for its `.pdf` -> `.docx` direction.
`PDFSinTablasError` is raised alongside these for its `.pdf` -> `.xlsx`
direction, when a PDF has extractable text but no detectable tables.

`OCRNoDisponibleError` and `OCRFallidaError` are raised by
`app.core.services.ocr_service` so `pytesseract`/Tesseract subprocess
failures never reach callers as raw tracebacks.

`PDFSinCoincidenciasError` is raised by `app.core.services.pdf_service`'s
`highlight_text`/`redact_text`, alongside `EntradaInvalidaError` and
`PDFCorruptoError`, when a text search finds zero matches in the
requested page scope.
"""

from __future__ import annotations


class ConfigError(Exception):
    """Raised when required application configuration is missing or invalid."""


class PDFCorruptoError(Exception):
    """Raised when a PDF input is structurally malformed or unparsable."""


class ArchivoProtegidoError(Exception):
    """Raised when an operation hits an already password-protected PDF."""


class ContrasenaInvalidaError(Exception):
    """Raised when an unlock password does not match the PDF's password."""


class EntradaInvalidaError(Exception):
    """Raised when caller-supplied input (paths, pages, images) is invalid."""


class ConversorNoDisponibleError(Exception):
    """Raised when the active document converter provider is unavailable."""


class ConversionFallidaError(Exception):
    """Raised when a document conversion fails or exceeds its timeout."""


class PDFSinTextoError(Exception):
    """Raised when a PDF has no extractable text layer (likely scanned or image-only)."""


class PDFSinTablasError(Exception):
    """Raised when a PDF has extractable text but no detectable tables to export."""


class OCRNoDisponibleError(Exception):
    """Raised when the active OCR provider is unavailable."""


class OCRFallidaError(Exception):
    """Raised when OCR recognition fails or exceeds its timeout."""


class PDFSinCoincidenciasError(Exception):
    """Raised when a highlight/redact text search finds no matches across
    the requested page scope."""
