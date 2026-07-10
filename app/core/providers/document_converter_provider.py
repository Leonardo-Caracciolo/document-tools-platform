"""`DocumentConverterProvider` Protocol, per SSD §5.1/§6.2.

Defines the structural contract every document-to-PDF conversion engine
(`ComWordProvider`, `AzureDocConverterProvider`, ...) must satisfy so
`app.core.services.export_service.ExportService` can select and inject a
provider without depending on any concrete implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class DocumentConverterProvider(Protocol):
    """Structural contract for a single-document-to-PDF conversion engine.

    Implementations own only the raw engine call; validation and error
    translation live in `ExportService`, which is the sole caller.
    """

    def convertir(self, source: Path, output: Path) -> Path:
        """Convert `source` to a PDF at `output` and return `output`.

        Implementations MAY raise any exception on failure — callers are
        expected to translate raw engine failures to the domain exceptions
        in `app.core.exceptions` at their own boundary.
        """
        ...

    def esta_disponible(self) -> tuple[bool, str]:
        """Return `(True, reason)` if this provider can perform a conversion.

        A cheap, non-invasive probe only — it MUST NOT launch or otherwise
        engage the underlying conversion engine. Informational only: it
        gates no v1 behavior on its own, callers decide whether/how to act
        on the result.
        """
        ...
