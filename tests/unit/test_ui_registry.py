"""Tests for `app.ui.registry` — PR1 scope (foundation, no Tk root), extended
in PR2 for `sdd/edit-pdf`'s `edit_pdf` (Family F) entry.

Covers `TOOL_SPECS`/`SPEC_BY_ID` shape (13 entries, unique ids, design §4
family partition), each `ToolSpec.run` lambda's off-thread wiring
(monkeypatched service classes — no real PDF/OCR/converter operation is
ever exercised here), `_EDIT_DISPATCH`/`_run_edit_pdf`'s per-mode wiring
(`sdd/edit-pdf/design` "Registry entry + dispatch"), and
`suggest_output_name`'s per-tool suffix/ext table (design §8).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core.services.pdf_service import SpanInfo
from app.ui import registry as registry_module
from app.ui.registry import (
    SPEC_BY_ID,
    TOOL_SPECS,
    Family,
    OutputKind,
    PanelValues,
    suggest_output_name,
)


class TestToolSpecsShape:
    def test_exactly_13_entries(self) -> None:
        assert len(TOOL_SPECS) == 13

    def test_spec_by_id_covers_all_tool_ids_uniquely(self) -> None:
        tool_ids = [spec.tool_id for spec in TOOL_SPECS]

        assert len(tool_ids) == len(set(tool_ids))
        assert set(SPEC_BY_ID) == set(tool_ids)
        for tool_id, spec in SPEC_BY_ID.items():
            assert spec.tool_id == tool_id

    def test_family_partition_matches_design_5_3_1_2_1_1(self) -> None:
        counts: dict[Family, int] = dict.fromkeys(Family, 0)
        for spec in TOOL_SPECS:
            counts[spec.family] += 1

        assert counts == {
            Family.A: 5,
            Family.B: 3,
            Family.C: 1,
            Family.D: 2,
            Family.E: 1,
            Family.F: 1,
        }

    def test_family_a_members(self) -> None:
        ids = {spec.tool_id for spec in TOOL_SPECS if spec.family is Family.A}
        assert ids == {"compress", "ocr", "convertir", "pdf_a_word", "pdf_a_excel"}

    def test_family_b_members(self) -> None:
        ids = {spec.tool_id for spec in TOOL_SPECS if spec.family is Family.B}
        assert ids == {"merge", "jpg_to_pdf", "scan_to_pdf"}

    def test_family_c_members(self) -> None:
        ids = {spec.tool_id for spec in TOOL_SPECS if spec.family is Family.C}
        assert ids == {"split"}

    def test_family_d_members(self) -> None:
        ids = {spec.tool_id for spec in TOOL_SPECS if spec.family is Family.D}
        assert ids == {"protect", "unlock"}

    def test_family_e_members(self) -> None:
        ids = {spec.tool_id for spec in TOOL_SPECS if spec.family is Family.E}
        assert ids == {"organize"}

    def test_family_f_members(self) -> None:
        ids = {spec.tool_id for spec in TOOL_SPECS if spec.family is Family.F}
        assert ids == {"edit_pdf"}

    def test_split_has_directory_output_kind_and_no_secret_fields(self) -> None:
        split = SPEC_BY_ID["split"]

        assert split.output_kind is OutputKind.DIRECTORY
        assert split.secret_fields == ()

    def test_protect_and_unlock_secret_fields(self) -> None:
        protect = SPEC_BY_ID["protect"]
        assert [field.key for field in protect.secret_fields] == [
            "owner_password",
            "user_password",
        ]
        assert protect.secret_fields[0].required is True
        assert protect.secret_fields[1].required is False

        unlock = SPEC_BY_ID["unlock"]
        assert [field.key for field in unlock.secret_fields] == ["password"]
        assert unlock.secret_fields[0].required is True

    def test_non_secret_specs_have_no_secret_fields(self) -> None:
        for spec in TOOL_SPECS:
            if spec.tool_id not in {"protect", "unlock"}:
                assert spec.secret_fields == ()


class TestPanelValuesDefaults:
    def test_selected_span_and_replacement_default_to_none(self) -> None:
        values = PanelValues()

        assert values.selected_span is None
        assert values.replacement is None


class TestRunWiring:
    """Each `ToolSpec.run` lambda is spied by monkeypatching the service
    class it constructs — no real PDF/OCR/converter operation runs."""

    def _patch_service(self, monkeypatch: pytest.MonkeyPatch, service_name: str) -> MagicMock:
        mock_cls = MagicMock(name=service_name)
        monkeypatch.setattr(registry_module, service_name, mock_cls)
        return mock_cls

    def test_merge_calls_pdf_service_merge_with_inputs_and_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        inputs = [Path("a.pdf"), Path("b.pdf")]
        output = Path("out.pdf")
        values = PanelValues(inputs=inputs, output=output)

        SPEC_BY_ID["merge"].run(values)

        mock_cls.return_value.merge.assert_called_once_with(inputs, output)

    def test_split_calls_pdf_service_split_with_ranges_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        source = Path("in.pdf")
        output_dir = Path("out_dir")
        values = PanelValues(source=source, output_dir=output_dir)

        SPEC_BY_ID["split"].run(values)

        mock_cls.return_value.split.assert_called_once_with(source, output_dir, ranges=None)

    def test_organize_calls_pdf_service_organize_with_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        source = Path("in.pdf")
        output = Path("out.pdf")
        order = [3, 1, 2]
        values = PanelValues(source=source, output=output, order=order)

        SPEC_BY_ID["organize"].run(values)

        mock_cls.return_value.organize.assert_called_once_with(source, output, order)

    def test_protect_calls_pdf_service_protect_with_owner_and_user_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        source = Path("in.pdf")
        output = Path("out.pdf")
        values = PanelValues(
            source=source,
            output=output,
            secrets={"owner_password": "owner123", "user_password": "user456"},
        )

        SPEC_BY_ID["protect"].run(values)

        mock_cls.return_value.protect.assert_called_once_with(
            source, output, "owner123", "user456"
        )

    def test_protect_maps_empty_optional_user_password_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        source = Path("in.pdf")
        output = Path("out.pdf")
        values = PanelValues(
            source=source,
            output=output,
            secrets={"owner_password": "owner123", "user_password": ""},
        )

        SPEC_BY_ID["protect"].run(values)

        mock_cls.return_value.protect.assert_called_once_with(source, output, "owner123", None)

    def test_unlock_calls_pdf_service_unlock_with_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        source = Path("in.pdf")
        output = Path("out.pdf")
        values = PanelValues(source=source, output=output, secrets={"password": "secret"})

        SPEC_BY_ID["unlock"].run(values)

        mock_cls.return_value.unlock.assert_called_once_with(source, output, "secret")

    def test_convertir_calls_export_service_convertir(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "ExportService")
        source = Path("in.docx")
        output = Path("out.pdf")
        values = PanelValues(source=source, output=output)

        SPEC_BY_ID["convertir"].run(values)

        mock_cls.return_value.convertir.assert_called_once_with(source, output)

    def test_pdf_a_word_calls_export_service_pdf_a_word(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "ExportService")
        source = Path("in.pdf")
        output = Path("out.docx")
        values = PanelValues(source=source, output=output)

        SPEC_BY_ID["pdf_a_word"].run(values)

        mock_cls.return_value.pdf_a_word.assert_called_once_with(source, output)

    def test_pdf_a_excel_calls_export_service_pdf_a_excel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "ExportService")
        source = Path("in.pdf")
        output = Path("out.xlsx")
        values = PanelValues(source=source, output=output)

        SPEC_BY_ID["pdf_a_excel"].run(values)

        mock_cls.return_value.pdf_a_excel.assert_called_once_with(source, output)

    def test_jpg_to_pdf_calls_pdf_service_jpg_to_pdf_with_inputs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        inputs = [Path("a.jpg"), Path("b.jpg")]
        output = Path("out.pdf")
        values = PanelValues(inputs=inputs, output=output)

        SPEC_BY_ID["jpg_to_pdf"].run(values)

        mock_cls.return_value.jpg_to_pdf.assert_called_once_with(inputs, output)

    def test_compress_calls_pdf_service_compress(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        source = Path("in.pdf")
        output = Path("out.pdf")
        values = PanelValues(source=source, output=output)

        SPEC_BY_ID["compress"].run(values)

        mock_cls.return_value.compress.assert_called_once_with(source, output)

    def test_ocr_calls_ocr_service_ocr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_cls = self._patch_service(monkeypatch, "OCRService")
        source = Path("in.pdf")
        output = Path("out.pdf")
        values = PanelValues(source=source, output=output)

        SPEC_BY_ID["ocr"].run(values)

        mock_cls.return_value.ocr.assert_called_once_with(source, output)

    def test_scan_to_pdf_calls_scan_service_scan_to_pdf_with_inputs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "ScanService")
        inputs = [Path("a.jpg"), Path("b.jpg")]
        output = Path("out.pdf")
        values = PanelValues(inputs=inputs, output=output)

        SPEC_BY_ID["scan_to_pdf"].run(values)

        mock_cls.return_value.scan_to_pdf.assert_called_once_with(inputs, output)

    def test_edit_pdf_add_text_mode_calls_pdf_service_add_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        source = Path("in.pdf")
        output = Path("out.pdf")
        values = PanelValues(
            mode="add_text",
            source=source,
            output=output,
            page=2,
            insert_text="hello",
            position="top-left",
        )

        SPEC_BY_ID["edit_pdf"].run(values)

        mock_cls.return_value.add_text.assert_called_once_with(
            source, output, 2, "hello", "top-left", None
        )

    def test_edit_pdf_add_text_mode_forwards_click_point(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        source = Path("in.pdf")
        output = Path("out.pdf")
        values = PanelValues(
            mode="add_text",
            source=source,
            output=output,
            page=2,
            insert_text="hello",
            position="top-left",
            point=(123.0, 456.0),
        )

        SPEC_BY_ID["edit_pdf"].run(values)

        mock_cls.return_value.add_text.assert_called_once_with(
            source, output, 2, "hello", "top-left", (123.0, 456.0)
        )

    def test_edit_pdf_highlight_text_mode_calls_pdf_service_highlight_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        source = Path("in.pdf")
        output = Path("out.pdf")
        values = PanelValues(
            mode="highlight_text",
            source=source,
            output=output,
            search_query="invoice",
            page=None,
        )

        SPEC_BY_ID["edit_pdf"].run(values)

        mock_cls.return_value.highlight_text.assert_called_once_with(
            source, output, "invoice", None
        )

    def test_edit_pdf_redact_text_mode_calls_pdf_service_redact_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        source = Path("in.pdf")
        output = Path("out.pdf")
        values = PanelValues(
            mode="redact_text",
            source=source,
            output=output,
            search_query="confidential",
            page=3,
        )

        SPEC_BY_ID["edit_pdf"].run(values)

        mock_cls.return_value.redact_text.assert_called_once_with(
            source, output, "confidential", 3
        )

    def test_edit_pdf_replace_text_mode_calls_pdf_service_replace_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cls = self._patch_service(monkeypatch, "PDFService")
        source = Path("in.pdf")
        output = Path("out.pdf")
        span = SpanInfo(
            text="Hello",
            bbox=(10.0, 10.0, 50.0, 20.0),
            origin=(10.0, 18.0),
            font="Helvetica",
            size=11.0,
            color=(0.0, 0.0, 0.0),
        )
        values = PanelValues(
            mode="replace_text",
            source=source,
            output=output,
            page=2,
            selected_span=span,
            replacement="new text",
        )

        SPEC_BY_ID["edit_pdf"].run(values)

        mock_cls.return_value.replace_text.assert_called_once_with(
            source, output, 2, span, "new text"
        )

    def test_all_13_tool_ids_have_wiring_coverage(self) -> None:
        # Regression guard: fails loudly if a new ToolSpec is added to
        # TOOL_SPECS without a matching wiring test above.
        tested_ids = {
            "merge",
            "split",
            "organize",
            "protect",
            "unlock",
            "convertir",
            "pdf_a_word",
            "pdf_a_excel",
            "jpg_to_pdf",
            "compress",
            "ocr",
            "scan_to_pdf",
            "edit_pdf",
        }
        assert tested_ids == set(SPEC_BY_ID)


class TestSuggestOutputName:
    @pytest.mark.parametrize(
        ("tool_id", "filename", "expected"),
        [
            ("compress", "invoice.pdf", "invoice_compressed.pdf"),
            ("ocr", "invoice.pdf", "invoice_ocr.pdf"),
            ("convertir", "report.docx", "report.pdf"),
            ("pdf_a_word", "invoice.pdf", "invoice.docx"),
            ("pdf_a_excel", "invoice.pdf", "invoice.xlsx"),
            ("merge", "a.pdf", "a_merged.pdf"),
            ("jpg_to_pdf", "a.jpg", "a.pdf"),
            ("scan_to_pdf", "a.jpg", "a_scanned.pdf"),
            ("protect", "invoice.pdf", "invoice_protected.pdf"),
            ("unlock", "invoice.pdf", "invoice_unlocked.pdf"),
            ("organize", "invoice.pdf", "invoice_organized.pdf"),
            ("edit_pdf", "invoice.pdf", "invoice_edited.pdf"),
        ],
    )
    def test_matches_per_tool_suffix_ext_table(
        self, tool_id: str, filename: str, expected: str
    ) -> None:
        spec = SPEC_BY_ID[tool_id]
        source = Path(filename)

        result = suggest_output_name(source, spec.output_suffix, spec.output_ext)

        assert result == expected

    def test_split_has_no_output_suffix_or_ext(self) -> None:
        split = SPEC_BY_ID["split"]

        assert split.output_suffix == ""
        assert split.output_ext == ""
