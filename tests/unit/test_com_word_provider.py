"""Unit tests for `ComWordProvider.esta_disponible()` — PR2 scope.

Only `esta_disponible()` is covered here: it's a cheap probe (platform +
import + registry checks) that never launches Word, so it's the one
`ComWordProvider` method that's safely unit-testable without real COM
automation. `convertir()`'s two-phase queue protocol — including the
orphan-`WINWORD.EXE` regression test — is covered by
`tests/integration/test_com_word_provider.py` instead, since it requires
real Word COM automation.

This dev machine has pywin32, docx2pdf, and a registered `Word.Application`
ProgID (confirmed empirically during design — see
`sdd/word-to-pdf-provider/design`), so the "available" branch CAN be
exercised for real, with no mocking — but GitHub-hosted `windows-latest`
CI runners do NOT have Office installed, so that test self-skips rather
than hardcoding an assumption only true on this machine. The "unavailable"
branches are exercised by monkeypatching `platform.system`/
`importlib.util.find_spec`/`winreg.OpenKey` individually, since this
machine can't otherwise produce those conditions.
"""

from __future__ import annotations

import importlib.util
import platform

import pytest

from app.core.providers.com_word_provider import ComWordProvider
from app.core.providers.document_converter_provider import DocumentConverterProvider


def test_com_word_provider_satisfies_the_protocol() -> None:
    provider = ComWordProvider()

    assert isinstance(provider, DocumentConverterProvider)


@pytest.mark.skipif(
    platform.system() != "Windows", reason="Word COM checks only apply on Windows"
)
def test_esta_disponible_reports_available_when_office_is_installed() -> None:
    """Real, unmocked probe against whatever this runner actually has.

    Self-skips rather than hardcoding availability: this dev machine has
    Office installed, but GitHub-hosted `windows-latest` CI runners do
    not — asserting `True` unconditionally would pass here and fail
    there. The negative-path tests below already cover the detection
    LOGIC via monkeypatching; this test only confirms the happy path is
    reachable on a machine where Office genuinely is present.
    """
    provider = ComWordProvider()

    available, reason = provider.esta_disponible()
    if not available:
        pytest.skip(f"Office/Word not available on this runner: {reason}")

    assert (available, reason) == (True, "COM/Word available")


def test_esta_disponible_reports_unavailable_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.providers.com_word_provider.platform.system", lambda: "Linux"
    )
    provider = ComWordProvider()

    available, reason = provider.esta_disponible()

    assert available is False
    assert "Windows" in reason


def test_esta_disponible_reports_unavailable_when_win32com_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_find_spec = importlib.util.find_spec

    def _fake_find_spec(name: str, *args: object, **kwargs: object) -> object:
        if name == "win32com":
            return None
        return original_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(
        "app.core.providers.com_word_provider.importlib.util.find_spec", _fake_find_spec
    )
    provider = ComWordProvider()

    available, reason = provider.esta_disponible()

    assert available is False
    assert "pywin32" in reason


def test_esta_disponible_reports_unavailable_when_docx2pdf_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_find_spec = importlib.util.find_spec

    def _fake_find_spec(name: str, *args: object, **kwargs: object) -> object:
        if name == "docx2pdf":
            return None
        return original_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(
        "app.core.providers.com_word_provider.importlib.util.find_spec", _fake_find_spec
    )
    provider = ComWordProvider()

    available, reason = provider.esta_disponible()

    assert available is False
    assert "docx2pdf" in reason


@pytest.mark.skipif(
    platform.system() != "Windows", reason="winreg is only importable on Windows"
)
def test_esta_disponible_reports_unavailable_when_word_progid_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_file_not_found(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(
        "app.core.providers.com_word_provider.winreg.OpenKey", _raise_file_not_found
    )
    provider = ComWordProvider()

    available, reason = provider.esta_disponible()

    assert available is False
    assert "Word.Application" in reason


@pytest.mark.skipif(
    platform.system() != "Windows", reason="win32com is only importable on Windows"
)
def test_esta_disponible_never_calls_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("esta_disponible() must never call Dispatch()")

    monkeypatch.setattr(
        "app.core.providers.com_word_provider.win32com.client.Dispatch", _fail_if_called
    )
    provider = ComWordProvider()

    provider.esta_disponible()  # must not raise
