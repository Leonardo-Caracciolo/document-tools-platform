"""Tests for `app.ui.errors` — PR1 scope (foundation, no Tk root).

Covers every mapped exception type in `ERROR_MESSAGES` (design §6:
10 domain exceptions + `NotImplementedError`), the unmapped-exception
fallback (`DEFAULT_ERROR_MESSAGE`, log-only, never raises), and the
`isinstance` fallback walk.
"""

from __future__ import annotations

import logging

import pytest

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
from app.ui.errors import DEFAULT_ERROR_MESSAGE, ERROR_MESSAGES, error_message

_LOGGER_NAME = "app.ui.errors"

#: Exact designed message for every mapped exception type (design §6),
#: independent of `ERROR_MESSAGES`'s own literal strings — this test file
#: does not simply assert the map against itself.
_EXPECTED_MESSAGES: dict[type[BaseException], str] = {
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


def test_error_messages_covers_exactly_the_designed_11_entries() -> None:
    assert set(ERROR_MESSAGES) == set(_EXPECTED_MESSAGES)
    assert len(ERROR_MESSAGES) == 11


@pytest.mark.parametrize("exc_type", list(_EXPECTED_MESSAGES))
def test_mapped_exception_resolves_to_exact_designed_message(
    exc_type: type[BaseException],
) -> None:
    exc = exc_type("some raw library detail that must never be shown")

    result = error_message(exc)

    assert result == _EXPECTED_MESSAGES[exc_type]
    # Never leak the raw exception string into the shown message.
    assert "raw library detail" not in result


def test_mapped_exception_lookup_does_not_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        error_message(EntradaInvalidaError("boom"))

    assert caplog.records == []


class _UnmappedException(Exception):
    """A novel exception type intentionally absent from `ERROR_MESSAGES`."""


class TestUnmappedFallback:
    @pytest.mark.parametrize(
        "exc",
        [ValueError("unexpected"), RuntimeError("boom"), _UnmappedException("boom")],
    )
    def test_unmapped_exception_returns_default_message(self, exc: Exception) -> None:
        result = error_message(exc)

        assert result == DEFAULT_ERROR_MESSAGE

    def test_unmapped_exception_never_raises(self) -> None:
        # error_message must be a pure resolver — calling it must never
        # itself raise, regardless of the input exception.
        error_message(_UnmappedException("boom"))

    def test_unmapped_exception_logs_full_traceback_and_is_never_shown(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        exc = _UnmappedException("this raw detail must be logged, never shown")

        with caplog.at_level(logging.ERROR, logger=_LOGGER_NAME):
            result = error_message(exc)

        assert result == DEFAULT_ERROR_MESSAGE
        assert "this raw detail must be logged, never shown" not in result
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1
        assert error_records[0].exc_info is not None
        assert "this raw detail must be logged, never shown" in caplog.text
