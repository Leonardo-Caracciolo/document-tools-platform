"""Tests for `format_success_message` — PR3 scope.

Pure function, no Tk root needed: `ToolView._on_success` (design §3)
formats a terminal `Path`/`list[Path]` result into a one-line status
message. `ToolView` itself is not automated in this PR — see the manual
QA checklist in `sdd/acrobat-tools-ui/apply-progress` for the covered
spinner/Run-disable/sidebar-switch lifecycle scenarios (tasks artifact's
explicit testing-strategy decision).
"""

from __future__ import annotations

from pathlib import Path

from app.ui.views.tool_view import format_success_message


class TestFormatSuccessMessage:
    def test_single_path_reports_saved_to(self) -> None:
        path = Path("out/invoice_compressed.pdf")

        result = format_success_message(path)

        assert result == f"Saved to: {path}"

    def test_path_list_reports_count_and_parent_dir(self) -> None:
        paths = [Path("out/page_1.pdf"), Path("out/page_2.pdf"), Path("out/page_3.pdf")]

        result = format_success_message(paths)

        assert result.startswith("Saved 3 files to: ")
        assert result.endswith("out")

    def test_empty_path_list_reports_zero_files(self) -> None:
        result = format_success_message([])

        assert result == "Saved 0 files to: "
