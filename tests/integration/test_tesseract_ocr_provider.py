"""Integration tests for `TesseractOCRProvider` — real Tesseract OCR.

Excluded from the default CI run (`pytest tests/unit`, see
`.github/workflows/ci.yml`) by living outside `tests/unit`; every test
here is additionally `skipif`-gated so it never fails hard on a runner
without Tesseract (or its Spanish language pack) installed — run
explicitly via `pytest tests/integration` on a machine with both. A
GitHub-hosted `windows-latest` CI runner will not have Tesseract
installed by default, same class of gap as `ComWordProvider`'s
Word-availability integration tests.

`test_reconocer_timeout_does_not_leak_orphaned_tesseract_process` is the
regression test locking in `sdd/ocr-pdf-provider/design`'s confirmed
Decision 3 finding: unlike `ComWordProvider`'s COM automation,
`pytesseract`'s own internal `subprocess.run(..., timeout=...)` correctly
tears down `tesseract.exe` on timeout with no orphan — no app-managed
two-phase PID protocol needed here. It should PASS given that confirmed
behavior; it exists so a future `pytesseract`/Tesseract version change
can't silently regress this assumption.
"""

from __future__ import annotations

import csv
import io
import subprocess
import time
from pathlib import Path

import pytesseract
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.core.providers.tesseract_ocr_provider import TesseractOCRProvider

_CANDIDATE_FONTS = (
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in _CANDIDATE_FONTS:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    pytest.skip("No usable TrueType font found to render OCR test text.")


def _tesseract_with_spanish_available() -> tuple[bool, str]:
    """Probe both binary availability AND Spanish language data.

    `TesseractOCRProvider.esta_disponible()` only resolves the binary, per
    its own cheap-probe contract — it deliberately does not check
    language data. These tests need `spa.traineddata` too (see
    `sdd/ocr-pdf-provider/design`'s "Deployment/onboarding finding" — the
    standard Windows Tesseract distribution does not bundle it), so the
    module-level skip gate checks both, real, unmocked.
    """
    provider = TesseractOCRProvider()
    available, reason = provider.esta_disponible()
    if not available:
        return False, reason
    try:
        langs = pytesseract.get_languages(config="")
    except Exception as exc:  # noqa: BLE001 - probing only, never raised to callers
        return False, f"could not query tesseract languages: {exc}"
    if "spa" not in langs:
        return False, "spa language data not installed (check TESSDATA_PREFIX)."
    return True, "available"


_SPANISH_AVAILABLE, _SPANISH_UNAVAILABLE_REASON = _tesseract_with_spanish_available()

pytestmark = pytest.mark.skipif(
    not _SPANISH_AVAILABLE,
    reason=f"Tesseract with Spanish language data unavailable: {_SPANISH_UNAVAILABLE_REASON}",
)


def _make_text_image(text: str, size: tuple[int, int] = (2550, 3300)) -> Image.Image:
    """Render `text` onto a white 300-DPI-sized page image via Pillow.

    Mirrors the orchestrator's empirical verification script
    (`sdd/ocr-pdf-provider/design`, "Empirical status" (b)): real,
    rendered glyphs at a known pixel position, not synthetic/random
    noise — Tesseract needs actual recognizable glyphs to produce
    word-level rows.
    """
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = _load_font(80)
    draw.text((150, 150), text, fill="black", font=font)
    return image


def _make_dense_image(size: tuple[int, int] = (4000, 5000)) -> Image.Image:
    """Render a large, text-dense image to make recognition take
    meaningfully longer than a near-zero timeout, so the timeout path
    actually triggers.
    """
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = _load_font(40)
    line = "Lorem ipsum dolor sit amet consectetur adipiscing elit " * 3
    y = 0
    while y < size[1]:
        draw.text((10, y), line, fill="black", font=font)
        y += 50
    return image


def _running_tesseract_pids() -> set[int]:
    """Return the PIDs of every currently-running `tesseract.exe` process.

    Mirrors `tests/integration/test_com_word_provider.py`'s
    `_running_word_pids` helper, gated on `tesseract.exe` instead of
    `WINWORD.EXE`.
    """
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq tesseract.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids: set[int] = set()
    for row in csv.reader(io.StringIO(result.stdout)):
        if len(row) < 2:
            continue
        try:
            pids.add(int(row[1]))
        except ValueError:
            continue
    return pids


def test_reconocer_recognizes_real_spanish_text() -> None:
    provider = TesseractOCRProvider()
    image = _make_text_image("Factura Total")

    words = provider.reconocer(image)

    recognized_text = {word.text for word in words}
    assert "Factura" in recognized_text
    assert "Total" in recognized_text

    for word in words:
        assert word.left >= 0
        assert word.top >= 0
        assert word.width > 0
        assert word.height > 0


def test_reconocer_timeout_does_not_leak_orphaned_tesseract_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the (absence of an) orphan-`tesseract.exe` bug.

    Forces an unrealistically short timeout (`0.01s`) against a large,
    text-dense image, mirroring the orchestrator's design-time repro.
    Expected outcome, per the confirmed design finding: a `RuntimeError`
    is raised AND no `tesseract.exe` process remains afterward — this is
    the opposite failure mode from `ComWordProvider`'s COM case, verified
    here rather than assumed.
    """
    provider = TesseractOCRProvider()
    monkeypatch.setattr(
        "app.core.providers.tesseract_ocr_provider._RECOGNITION_TIMEOUT_SECONDS", 0.01
    )
    image = _make_dense_image()

    pids_before = _running_tesseract_pids()

    with pytest.raises(RuntimeError):
        provider.reconocer(image)

    # Give the OS a brief moment to finish tearing down any transient
    # process before asserting — not necessarily instantaneous.
    deadline = time.monotonic() + 10.0
    orphaned = _running_tesseract_pids() - pids_before
    while orphaned and time.monotonic() < deadline:
        time.sleep(0.5)
        orphaned = _running_tesseract_pids() - pids_before

    assert not orphaned, (
        f"Orphaned tesseract.exe process(es) left running after timeout: {orphaned}"
    )
