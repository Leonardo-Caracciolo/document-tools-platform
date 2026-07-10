"""Azure OCR provider stub, per SSD §6.1.

Selectable via `OCR_PROVIDER=azure_di`. Raising `NotImplementedError`
from `reconocer` is the v1 deliverable for this provider, not a
placeholder to be excluded from testing — see `sdd/ocr-pdf-provider/spec`,
Requirement: Azure Stub Provider Behavior.
"""

from __future__ import annotations

from pathlib import Path

from app.core.providers.ocr_provider import RecognizedWord


class AzureOCRProvider:
    """`OCRProvider` implementation backed by Azure Document Intelligence — not yet implemented."""

    def reconocer(self, image: Path) -> list[RecognizedWord]:
        """Raise `NotImplementedError` — Azure OCR is not implemented."""
        raise NotImplementedError(
            "Azure OCR is not implemented; set OCR_PROVIDER=tesseract."
        )

    def esta_disponible(self) -> tuple[bool, str]:
        """Always report unavailable — Azure OCR is not implemented."""
        return False, "Azure provider not implemented"
