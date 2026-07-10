"""`TesseractOCRProvider` — local Tesseract OCR via `pytesseract`, per SSD §6.1.

`TesseractOCRProvider` implements `OCRProvider` (see
`app.core.providers.ocr_provider`) by shelling out to a local `tesseract`
binary through `pytesseract.image_to_data`.

Empirically confirmed during design (`sdd/ocr-pdf-provider/design`,
"Empirical status" (c)): unlike `ComWordProvider`'s COM automation, no
two-phase PID-tracking protocol is needed here. `pytesseract` owns its own
`subprocess.run(..., timeout=...)` call internally, and a forced timeout
against a large, text-dense image was confirmed (via `tasklist` before/
after) to leave zero orphaned `tesseract.exe` processes — a plain
`timeout=` kwarg is sufficient, confirmed sufficient, not just assumed.
On timeout, `pytesseract` raises `RuntimeError("Tesseract process
timeout")`, which this provider intentionally lets propagate uncaught —
see `reconocer`'s docstring.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytesseract

from app.core.exceptions import OCRNoDisponibleError
from app.core.providers.ocr_provider import RecognizedWord
from app.infrastructure.logger import get_logger

if TYPE_CHECKING:
    from PIL import Image

#: Overall wall-clock deadline for one `image_to_data` call, per design's
#: confirmed Decision 3. Folded into `OCRFallidaError` by `OCRService`'s
#: `_translate_provider_errors` boundary (PR3) — this module does not
#: import or raise that domain exception itself.
_RECOGNITION_TIMEOUT_SECONDS = 60.0

#: Typical install location for the UB-Mannheim Windows Tesseract
#: distribution (`winget install UB-Mannheim.TesseractOCR`), per SSD §6.1's
#: binary-autodetection fallback order.
_TYPICAL_WINDOWS_INSTALL_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


class TesseractOCRProvider:
    """`OCRProvider` implementation backed by a local Tesseract binary."""

    def __init__(self) -> None:
        self._log = get_logger(__name__)

    def esta_disponible(self) -> tuple[bool, str]:
        """Cheap probe: resolve the `tesseract` binary path only.

        Deliberately does NOT invoke `tesseract.exe` (e.g. `--version`) —
        doing so would launch a real process just to answer this question,
        mirroring `ComWordProvider.esta_disponible()`'s discipline of never
        engaging the underlying engine. Resolution order, per SSD §6.1:
        1. `TESSERACT_PATH` env var, if set and the file exists.
        2. `shutil.which("tesseract")` (binary on `PATH`).
        3. The typical Windows install path.

        On success, also sets `pytesseract.pytesseract.tesseract_cmd` to
        the resolved path so `reconocer` doesn't need to re-resolve it.
        """
        env_path = os.environ.get("TESSERACT_PATH")
        if env_path and Path(env_path).is_file():
            pytesseract.pytesseract.tesseract_cmd = env_path
            return True, env_path

        which_path = shutil.which("tesseract")
        if which_path:
            pytesseract.pytesseract.tesseract_cmd = which_path
            return True, which_path

        if _TYPICAL_WINDOWS_INSTALL_PATH.is_file():
            resolved = str(_TYPICAL_WINDOWS_INSTALL_PATH)
            pytesseract.pytesseract.tesseract_cmd = resolved
            return True, resolved

        return False, "tesseract binary not found (TESSERACT_PATH, PATH, or typical install path)."

    def reconocer(self, image: Image.Image) -> list[RecognizedWord]:
        """Recognize Spanish text in `image` via `pytesseract.image_to_data`.

        Raises:
            OCRNoDisponibleError: `esta_disponible()` reports Tesseract is
                not available.
            RuntimeError: raised by `pytesseract` itself when the
                recognition subprocess exceeds `_RECOGNITION_TIMEOUT_SECONDS`
                (message `"Tesseract process timeout"`, confirmed during
                design) or otherwise fails. Left uncaught here —
                `OCRService`'s `_translate_provider_errors` boundary (PR3)
                maps it to `OCRFallidaError`; this provider does not import
                or raise that domain exception.

        Filters `image_to_data`'s row-per-token output down to real,
        recognized words: `text.strip()` non-empty AND `conf != -1` (Tesseract
        marks non-word-level rows — block/paragraph/line — with `conf == -1`;
        confirmed empirically during design). `conf` is read from the raw
        dict for filtering purposes only — it is never stored on the
        returned `RecognizedWord`, which stays free of provider-specific
        fields (see `RecognizedWord`'s docstring).
        """
        available, reason = self.esta_disponible()
        if not available:
            self._log.warning("TesseractOCRProvider unavailable: %s", reason)
            raise OCRNoDisponibleError(reason)

        data = pytesseract.image_to_data(
            image,
            lang="spa",
            timeout=_RECOGNITION_TIMEOUT_SECONDS,
            output_type=pytesseract.Output.DICT,
        )

        words: list[RecognizedWord] = []
        for text, conf, left, top, width, height in zip(
            data["text"],
            data["conf"],
            data["left"],
            data["top"],
            data["width"],
            data["height"],
            strict=True,
        ):
            if not text.strip() or conf == -1:
                continue
            words.append(
                RecognizedWord(text=text, left=left, top=top, width=width, height=height)
            )
        return words
