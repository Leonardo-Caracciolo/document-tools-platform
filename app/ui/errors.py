"""Shared exception -> human-readable message map, per `sdd/acrobat-tools-ui/design` §6.

ONE map, not one per view: `ToolView._on_error` (PR3) AND every family
panel's `collect()` local guard (PR2, ADR-004 — a panel raises
`EntradaInvalidaError` locally for missing/malformed input, funneling
through this SAME resolver) both call `error_message(exc)`. This is the
single place a raised exception becomes user-facing text; a raw traceback
must never reach the UI (spec's "Exception-to-Message Mapping"
requirement).

All domain exceptions in `app.core.exceptions` are flat `Exception`
subclasses with no inheritance hierarchy among them, and `NotImplementedError`
is never subclassed by anything this app raises — so exact `type(exc)`
lookup covers every mapped case. No `isinstance` fallback is needed.
"""

from __future__ import annotations

from app.core.exceptions import (
    ArchivoProtegidoError,
    ContrasenaInvalidaError,
    ConversionFallidaError,
    ConversorNoDisponibleError,
    EntradaInvalidaError,
    OCRFallidaError,
    OCRNoDisponibleError,
    PDFCorruptoError,
    PDFSinTablasError,
    PDFSinTextoError,
)
from app.infrastructure.logger import get_logger

_logger = get_logger(__name__)

#: Exception type -> human-readable message, matched by exact `type(exc)`.
ERROR_MESSAGES: dict[type[Exception], str] = {
    EntradaInvalidaError: (
        "The input isn't valid. Check the selected file(s) and values, then try again."
    ),
    PDFCorruptoError: "This PDF appears to be damaged or unreadable.",
    ArchivoProtegidoError: "This PDF is password-protected. Unlock it first, then retry.",
    ContrasenaInvalidaError: "The password doesn't match this PDF.",
    ConversorNoDisponibleError: (
        "The document converter isn't available with the current configuration."
    ),
    ConversionFallidaError: "The conversion failed. Please try again.",
    PDFSinTextoError: (
        "This PDF has no extractable text (it may be a scanned image). Run OCR first."
    ),
    PDFSinTablasError: "No tables were found in this PDF to export.",
    OCRNoDisponibleError: "OCR isn't available with the current configuration.",
    OCRFallidaError: "Text recognition failed. Please try again.",
    NotImplementedError: "This feature isn't available with the current configuration.",
}

#: Shown for any exception type not present in `ERROR_MESSAGES` (by exact
#: type). The unmapped exception's full traceback is logged (never shown)
#: before this is returned.
DEFAULT_ERROR_MESSAGE = "The operation failed. Please try again."


def error_message(exc: Exception) -> str:
    """Resolve `exc` to a human-readable message, never a raw traceback.

    Looked up by exact `type(exc)`. If unmapped, the full traceback is
    logged at ERROR level (log only — never returned/shown) and
    `DEFAULT_ERROR_MESSAGE` is returned instead.
    """
    msg = ERROR_MESSAGES.get(type(exc))
    if msg is None:
        _logger.exception("Unmapped UI error", exc_info=exc)
        return DEFAULT_ERROR_MESSAGE
    return msg
