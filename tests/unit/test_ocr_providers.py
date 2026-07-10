"""Tests for `app.core.providers` OCR contract — PR1 scope only.

Covers `OCRProvider` Protocol conformance, the `RecognizedWord` dataclass,
and the `AzureOCRProvider` stub. `TesseractOCRProvider` (PR2) is not
covered here.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.core.providers.azure_ocr_provider import AzureOCRProvider
from app.core.providers.ocr_provider import OCRProvider, RecognizedWord


def test_recognized_word_is_a_frozen_dataclass() -> None:
    word = RecognizedWord(text="Factura", left=97, top=146, width=47, height=14, conf=91)

    assert word.text == "Factura"
    assert (word.left, word.top, word.width, word.height, word.conf) == (97, 146, 47, 14, 91)

    with pytest.raises(FrozenInstanceError):
        word.text = "Total"  # type: ignore[misc]


def test_azure_provider_satisfies_the_protocol() -> None:
    provider = AzureOCRProvider()

    assert isinstance(provider, OCRProvider)


def test_azure_provider_reconocer_raises_not_implemented(tmp_path: Path) -> None:
    provider = AzureOCRProvider()
    image = tmp_path / "page.png"

    with pytest.raises(NotImplementedError) as exc_info:
        provider.reconocer(image)

    assert str(exc_info.value) == (
        "Azure OCR is not implemented; set OCR_PROVIDER=tesseract."
    )


def test_azure_provider_esta_disponible_reports_unavailable() -> None:
    provider = AzureOCRProvider()

    assert provider.esta_disponible() == (False, "Azure provider not implemented")
