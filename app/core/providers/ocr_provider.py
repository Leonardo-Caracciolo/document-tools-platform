"""`OCRProvider` Protocol, per SSD §6.1.

Defines the structural contract every OCR recognition engine
(`TesseractOCRProvider`, `AzureOCRProvider`, ...) must satisfy so
`app.core.services.ocr_service.OCRService` can select and inject a
provider without depending on any concrete implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from PIL import Image


@dataclass(frozen=True)
class RecognizedWord:
    """A single OCR-recognized word with its page-pixel bounding box.

    `left`, `top`, `width`, `height` are pixel-space coordinates at the
    rasterization DPI the recognized image was produced at — callers are
    responsible for transforming them into the target coordinate space
    (e.g. PDF point space) before overlaying text. Deliberately has NO
    confidence/engine-specific field: low-confidence and non-word rows
    (e.g. `pytesseract.image_to_data`'s block/paragraph/line-level
    entries) are filtered out by the provider BEFORE constructing a
    `RecognizedWord` — every instance that reaches `OCRService` already
    represents a real, usable word. Keeping this type free of
    provider-specific fields (like Tesseract's particular confidence
    scale) is what keeps it portable to a future Azure provider.
    """

    text: str
    left: int
    top: int
    width: int
    height: int


@runtime_checkable
class OCRProvider(Protocol):
    """Structural contract for a single-image OCR recognition engine.

    Implementations own only the raw engine call; validation and error
    translation live in `OCRService`, which is the sole caller.
    """

    def reconocer(self, image: Image.Image) -> list[RecognizedWord]:
        """Recognize text in `image` (an already-rasterized page) and
        return one `RecognizedWord` per recognized word.

        Plain-text-only output is insufficient — callers need per-word
        position data to overlay a positionally-aligned searchable text
        layer. Takes an in-memory image, not a file path: `OCRService`
        rasterizes each PDF page directly to a `PIL.Image` (via PyMuPDF's
        `Page.get_pixmap`) and passes it straight through — no temp file
        round-trip. Implementations MAY raise any exception on failure —
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
