"""Shared exception -> human-readable message map, per `sdd/acrobat-tools-ui/design` §6.

ONE map, not one per view: `ToolView._on_error` (PR3) AND every family
panel's `collect()` local guard (PR2, ADR-004 — a panel raises
`EntradaInvalidaError` locally for missing/malformed input, funneling
through this SAME resolver) both call `error_message(exc)`. This is the
single place a raised exception becomes user-facing text; a raw traceback
must never reach the UI (spec's "Exception-to-Message Mapping"
requirement).

All domain exceptions in `app.core.exceptions` are flat `Exception`
subclasses with no inheritance hierarchy among them, so exact `type(exc)`
lookup is sufficient for them; the `isinstance` fallback walk exists for
builtin-subclass edge cases (e.g. a `TimeoutError` subclass that isn't
`NotImplementedError` itself, or any other future non-exact-match case).
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

#: Exception type -> human-readable message. Order matters only for the
#: `isinstance` fallback walk in `error_message` below (dict iteration
#: order is insertion order in Python 3.7+).
ERROR_MESSAGES: dict[type[BaseException], str] = {
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
#: type or `isinstance` fallback). The unmapped exception's full traceback
#: is logged (never shown) before this is returned.
DEFAULT_ERROR_MESSAGE = "The operation failed. Please try again."


def error_message(exc: BaseException) -> str:
    """Resolve `exc` to a human-readable message, never a raw traceback.

    Lookup order: exact `type(exc)` match first, then an `isinstance`
    fallback walk over `ERROR_MESSAGES` (covers a builtin-subclass edge
    case an exact match would miss). If neither matches, the full
    traceback is logged at ERROR level (log only — never returned/shown)
    and `DEFAULT_ERROR_MESSAGE` is returned instead.
    """
    msg = ERROR_MESSAGES.get(type(exc))
    if msg is None:
        for exc_type, candidate in ERROR_MESSAGES.items():
            if isinstance(exc, exc_type):
                msg = candidate
                break
    if msg is None:
        _logger.exception("Unmapped UI error", exc_info=exc)
        return DEFAULT_ERROR_MESSAGE
    return msg
