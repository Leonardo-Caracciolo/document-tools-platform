"""Azure document-converter provider stub, per SSD §5.1/§6.2.

Selectable via `DOC_CONVERTER_PROVIDER=azure`. Raising `NotImplementedError`
from `convertir` is the v1 deliverable for this provider, not a placeholder
to be excluded from testing — see `sdd/word-to-pdf-provider/spec`,
Requirement: Azure Stub Provider Behavior.
"""

from __future__ import annotations

from pathlib import Path


class AzureDocConverterProvider:
    """`DocumentConverterProvider` implementation backed by Azure — not yet implemented."""

    def convertir(self, source: Path, output: Path) -> Path:
        """Raise `NotImplementedError` — Azure conversion is not implemented."""
        raise NotImplementedError(
            "Azure document conversion is not implemented; set DOC_CONVERTER_PROVIDER=com."
        )

    def esta_disponible(self) -> tuple[bool, str]:
        """Always report unavailable — Azure conversion is not implemented."""
        return False, "Azure provider not implemented"
