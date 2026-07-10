"""Unit tests for `TesseractOCRProvider` — PR2 scope.

Covers `esta_disponible()`'s binary-resolution logic and `reconocer()`'s
unavailable-provider guard. The "unavailable" branches (no
`TESSERACT_PATH`, `shutil.which` returns `None`, typical path missing) are
exercised via monkeypatching so they run identically on any machine/CI
runner. The "available" branch is ALSO exercised for real (this dev
machine genuinely has Tesseract installed), but self-skips if not —
mirroring the lesson from `word-to-pdf-provider`'s CI fix: a
GitHub-hosted `windows-latest` runner will not have Tesseract installed
either, so this must never be an unconditional assertion.

Real recognition (`reconocer`'s happy path, timeout/no-orphan regression)
is integration-test scope — see `tests/integration/test_tesseract_ocr_provider.py`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.core.exceptions import OCRNoDisponibleError
from app.core.providers import tesseract_ocr_provider as tesseract_ocr_provider_module
from app.core.providers.tesseract_ocr_provider import TesseractOCRProvider


def test_esta_disponible_uses_tesseract_path_env_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_binary = tmp_path / "tesseract.exe"
    fake_binary.write_bytes(b"not a real binary, existence is all that matters")
    monkeypatch.setenv("TESSERACT_PATH", str(fake_binary))

    provider = TesseractOCRProvider()
    available, reason = provider.esta_disponible()

    assert available is True
    assert reason == str(fake_binary)


def test_esta_disponible_ignores_tesseract_path_env_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing_binary = tmp_path / "does-not-exist.exe"
    monkeypatch.setenv("TESSERACT_PATH", str(missing_binary))
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        tesseract_ocr_provider_module,
        "_TYPICAL_WINDOWS_INSTALL_PATH",
        tmp_path / "also-does-not-exist.exe",
    )

    provider = TesseractOCRProvider()
    available, reason = provider.esta_disponible()

    assert available is False
    assert "not found" in reason


def test_esta_disponible_falls_back_to_shutil_which(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TESSERACT_PATH", raising=False)
    which_path = str(tmp_path / "tesseract-on-path.exe")
    monkeypatch.setattr(shutil, "which", lambda _name: which_path)

    provider = TesseractOCRProvider()
    available, reason = provider.esta_disponible()

    assert available is True
    assert reason == which_path


def test_esta_disponible_falls_back_to_typical_windows_install_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TESSERACT_PATH", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    typical_path = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    typical_path.parent.mkdir()
    typical_path.write_bytes(b"not a real binary, existence is all that matters")
    monkeypatch.setattr(
        tesseract_ocr_provider_module, "_TYPICAL_WINDOWS_INSTALL_PATH", typical_path
    )

    provider = TesseractOCRProvider()
    available, reason = provider.esta_disponible()

    assert available is True
    assert reason == str(typical_path)


def test_esta_disponible_reports_unavailable_when_nothing_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TESSERACT_PATH", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        tesseract_ocr_provider_module,
        "_TYPICAL_WINDOWS_INSTALL_PATH",
        tmp_path / "nowhere.exe",
    )

    provider = TesseractOCRProvider()
    available, reason = provider.esta_disponible()

    assert available is False
    assert reason == (
        "tesseract binary not found (TESSERACT_PATH, PATH, or typical install path)."
    )


def test_esta_disponible_real_machine() -> None:
    """Exercises the real, unmocked resolution logic on this machine.

    Self-skips if Tesseract is genuinely not installed/resolvable here —
    a GitHub-hosted `windows-latest` CI runner will not have it, same
    class of problem as the Office-availability test fixed in
    `word-to-pdf-provider`.
    """
    provider = TesseractOCRProvider()
    available, reason = provider.esta_disponible()

    if not available:
        pytest.skip(f"Tesseract not available on this machine: {reason}")

    assert available is True
    assert reason


def test_reconocer_raises_ocr_no_disponible_error_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    provider = TesseractOCRProvider()
    monkeypatch.setattr(
        provider, "esta_disponible", lambda: (False, "tesseract binary not found")
    )

    with pytest.raises(OCRNoDisponibleError):
        provider.reconocer(Image.new("RGB", (10, 10), "white"))
