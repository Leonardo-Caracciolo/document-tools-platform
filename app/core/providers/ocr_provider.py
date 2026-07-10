"""`OCRProvider` Protocol, per SSD §6.1.

Defines the structural contract every OCR recognition engine
(`TesseractOCRProvider`, `AzureOCRProvider`, ...) must satisfy so
`app.core.services.ocr_service.OCRService` can select and inject a
provider without depending on any concrete implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RecognizedWord:
    """A single OCR-recognized word with its page-pixel bounding box.

    `left`, `top`, `width`, `height` are pixel-space coordinates at the
    rasterization DPI the recognized `image` was produced at — callers are
    responsible for transforming them into the target coordinate space
    (e.g. PDF point space) before overlaying text. `conf` is the engine's
    confidence score for this word, per `pytesseract.image_to_data`'s
    `conf` column.
    """

    text: str
    left: int
    top: int
    width: int
    height: int
    conf: int


@runtime_checkable
class OCRProvider(Protocol):
    """Structural contract for a single-image OCR recognition engine.

    Implementations own only the raw engine call; validation and error
    translation live in `OCRService`, which is the sole caller.
    """

    def reconocer(self, image: Path) -> list[RecognizedWord]:
        """Recognize text in `image` and return one `RecognizedWord` per word.

        Plain-text-only output is insufficient — callers need per-word
        position data to overlay a positionally-aligned searchable text
        layer. Implementations MAY raise any exception on failure —
        callers are expected to translate raw engine failures to the
        domain exceptions in `app.core.exceptions` at their own boundary.
        """
        ...

    def esta_disponible(self) -> tuple[bool, str]:
        """Return `(True, reason)` if this provider can perform recognition.

        A cheap, non-invasive probe only — it MUST NOT launch or otherwise
        engage the underlying recognition engine. Informational only: it
        gates no v1 behavior on its own, callers decide whether/how to act
        on the result.
        """
        ...
