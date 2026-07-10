"""Tests for `app.core.providers` — PR1 scope only.

Covers `DocumentConverterProvider` Protocol conformance and the
`AzureDocConverterProvider` stub. `ComWordProvider` (PR2) is not covered
here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.providers.azure_doc_converter_provider import AzureDocConverterProvider
from app.core.providers.document_converter_provider import DocumentConverterProvider


def test_azure_provider_satisfies_the_protocol() -> None:
    provider = AzureDocConverterProvider()

    assert isinstance(provider, DocumentConverterProvider)


def test_azure_provider_convertir_raises_not_implemented(tmp_path: Path) -> None:
    provider = AzureDocConverterProvider()
    source = tmp_path / "source.docx"
    output = tmp_path / "output.pdf"

    with pytest.raises(NotImplementedError) as exc_info:
        provider.convertir(source, output)

    assert str(exc_info.value) == (
        "Azure document conversion is not implemented; set DOC_CONVERTER_PROVIDER=com."
    )


def test_azure_provider_esta_disponible_reports_unavailable() -> None:
    provider = AzureDocConverterProvider()

    assert provider.esta_disponible() == (False, "Azure provider not implemented")
