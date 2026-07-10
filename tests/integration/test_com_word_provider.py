"""Integration tests for `ComWordProvider` — real Word COM automation.

Excluded from the default CI run (`pytest tests/unit`, see
`.github/workflows/ci.yml`) by living outside `tests/unit`; every test
here is additionally `skipif`-gated so it never fails hard on a
non-Windows/no-Office runner if invoked directly. Run explicitly via
`pytest tests/integration` on a machine with Word installed.

`test_convertir_timeout_does_not_leak_orphaned_word_process` is the
regression test for a real bug found during design's empirical
validation (`sdd/word-to-pdf-provider/design`, "Empirical status" (b)):
a naive "just terminate() the child process on timeout" approach leaves
an orphaned `WINWORD.EXE` process running, because killing the Python
child does not tear down the out-of-process COM server it activated.
The fix — the two-phase queue protocol implemented in
`app.core.providers.com_word_provider` — is verified here by forcing an
unrealistically short deadline and confirming zero `WINWORD.EXE`
processes remain afterward.
"""

from __future__ import annotations

import platform
from pathlib import Path

import pytest
from docx import Document

from app.core.providers.com_word_provider import ComWordProvider
from tests.integration._process_utils import assert_no_orphaned_process, running_pids_for_image

pytestmark = pytest.mark.skipif(
    platform.system() != "Windows", reason="ComWordProvider requires Windows + Word COM."
)


def _make_valid_docx(path: Path) -> Path:
    """Write a minimal real `.docx` via `python-docx` to `path`.

    Confirmed during design to be accepted by real Word COM automation
    and to convert successfully (see design's "make_valid_docx fixture
    note"). Not reusing `tests/fixtures/pdf_factory.py` (PDF-only) or a
    committed `docx_factory.py` module — that shared fixture module is
    PR3 scope (task 3.5); this is a small local helper for PR2's
    integration tests only.
    """
    document = Document()
    document.add_paragraph("ComWordProvider integration test content.")
    document.save(path)
    return path


def _skip_if_word_unavailable(provider: ComWordProvider) -> None:
    available, reason = provider.esta_disponible()
    if not available:
        pytest.skip(f"Word/COM unavailable on this runner: {reason}")


def test_convertir_produces_a_real_pdf(tmp_path: Path) -> None:
    provider = ComWordProvider()
    _skip_if_word_unavailable(provider)

    source = _make_valid_docx(tmp_path / "source.docx")
    output = tmp_path / "output.pdf"

    result = provider.convertir(source, output)

    assert result == output.resolve()
    assert output.is_file()
    assert output.stat().st_size > 0
    assert output.read_bytes()[:5] == b"%PDF-"


def test_convertir_timeout_does_not_leak_orphaned_word_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the orphan-`WINWORD.EXE` bug found during design.

    Forces a ~1.5s deadline — far shorter than the ~6s a real conversion
    takes (per design's empirical timing, including first-call Word
    startup) — so the timeout path actually triggers. Mirrors the exact
    deadline the orchestrator used to originally repro the bug (~1s) at
    design time.
    """
    provider = ComWordProvider()
    _skip_if_word_unavailable(provider)

    monkeypatch.setattr("app.core.providers.com_word_provider._TIMEOUT_SECONDS", 1.5)

    source = _make_valid_docx(tmp_path / "source.docx")
    output = tmp_path / "output.pdf"

    pids_before = running_pids_for_image("WINWORD.EXE")

    with pytest.raises(TimeoutError):
        provider.convertir(source, output)

    assert_no_orphaned_process("WINWORD.EXE", pids_before)
