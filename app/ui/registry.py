"""Data-driven tool registry — per SSD's "una vista por herramienta" and
`sdd/acrobat-tools-ui/design` §5/§8.

`TOOL_SPECS` is the single source of truth for all 13 UI-wired tool
operations: each entry is a `ToolSpec` pairing a sidebar label/group/input
family with an off-thread `run` callable that constructs the relevant
service and calls its method. `SPEC_BY_ID` is the lookup `MainWindow` uses
when a sidebar button is clicked (design §5).

Per the design's acyclic import rule, this module imports ONLY the 4
service classes, `app.core.exceptions` types are not needed here, and
`Path`/`Callable`/`Sequence` typing — it never imports from
`app.ui.widgets` or `app.ui.views`, so those modules can safely import
`registry` without a cycle.

`PanelValues` lives here (not in `widgets/panels.py`) for the same reason:
both `ToolSpec.run` callables (below) and each family panel's `collect()`
(PR2) need the identical type without either module importing the other.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from app.core.services.export_service import ExportService
from app.core.services.ocr_service import OCRService
from app.core.services.pdf_service import PDFService
from app.core.services.scan_service import ScanService


class Family(Enum):
    """Input-shape family a `ToolSpec` belongs to (design §4, A-E)."""

    A = auto()  # single-in / single-out
    B = auto()  # multi-in / single-out
    C = auto()  # single-in / dir-out (split)
    D = auto()  # secret(s) + single-in / single-out (protect, unlock)
    E = auto()  # ordered-ints + single-in / single-out (organize)
    F = auto()  # mode-selector: single-in / single-out, internal mode dropdown (edit_pdf)


class OutputKind(Enum):
    """Whether a `ToolSpec`'s output is a single file or a directory."""

    FILE = auto()
    DIRECTORY = auto()


@dataclass(frozen=True)
class SecretField:
    """One masked input field for a Family D (`protect`/`unlock`) panel."""

    key: str
    label: str
    required: bool


@dataclass
class PanelValues:
    """Values collected from an `InputPanel.collect()` call (PR2).

    Every family populates only the fields its `ToolSpec.run` callable
    reads — the rest stay at their `None`/empty default. Not frozen:
    panels build this incrementally as they read their own widgets.
    """

    source: Path | None = None
    inputs: Sequence[Path] | None = None
    output: Path | None = None
    output_dir: Path | None = None
    order: Sequence[int] | None = None
    secrets: dict[str, str] | None = None
    mode: str | None = None
    page: int | None = None
    insert_text: str | None = None
    search_query: str | None = None
    position: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """Declarative description of one of the 13 UI-wired tool operations.

    `run` is the OFF-thread service call (constructed and invoked on the
    `TaskRunner` worker thread, per design ADR-005 — never on the UI
    thread, never cached on a view). `output_suffix`/`output_ext`/
    `output_kind` drive `SaveAsRow`'s filename suggestion (design §8,
    PR2/PR3). `secret_fields` is populated only for Family D specs.
    """

    tool_id: str
    label: str
    group: str
    family: Family
    run: Callable[[PanelValues], Path | list[Path]]
    output_suffix: str = ""
    output_ext: str = ""
    output_kind: OutputKind = OutputKind.FILE
    secret_fields: tuple[SecretField, ...] = ()
    #: Restrained single-glyph sidebar icon (design polish pass — cosmetic
    #: only). Trailing field with a default so existing positional/keyword
    #: `ToolSpec(...)` construction stays valid.
    icon: str = ""


def suggest_output_name(source: Path, suffix: str, ext: str) -> str:
    """Return a suggested output filename derived from `source`'s stem.

    Pure helper (design §8) — `SaveAsRow` (PR2) calls this whenever the
    source selection changes, to pre-fill the save dialog's
    `initialfile`. `ext` may be `""` (no `defaultextension` override) and
    `suffix` may be `""` (extension-only change, e.g. `convertir`).
    """
    return f"{source.stem}{suffix}{ext}"


#: Mode -> off-thread dispatch lambda for `edit_pdf` (Family F). Keyed by
#: the same mode strings `EditPanel.collect()` sets on `PanelValues.mode` —
#: `EditPanel`'s local guard (ADR-004) guarantees `v.mode` is always one of
#: these 3 keys by the time `_run_edit_pdf` reads it, so no `KeyError`
#: fallback is needed (design §"Registry entry + dispatch").
_EDIT_DISPATCH: dict[str, Callable[[PDFService, PanelValues], Path]] = {
    "add_text": lambda s, v: s.add_text(v.source, v.output, v.page, v.insert_text, v.position),
    "highlight_text": lambda s, v: s.highlight_text(v.source, v.output, v.search_query, v.page),
    "redact_text": lambda s, v: s.redact_text(v.source, v.output, v.search_query, v.page),
}


def _run_edit_pdf(v: PanelValues) -> Path:
    """Dispatch `edit_pdf`'s off-thread run to the mode-appropriate method."""
    return _EDIT_DISPATCH[v.mode](PDFService(), v)


#: Ordered, grouped registry of all 13 UI-wired tool operations — sidebar
#: build order in `MainWindow` (PR3) follows this tuple's order exactly
#: (design §5). Grouped: Organize (merge, split, organize), Secure
#: (protect, unlock), Convert (convertir, pdf_a_word, pdf_a_excel,
#: jpg_to_pdf, compress), Recognize (ocr, scan_to_pdf), Edit (edit_pdf).
TOOL_SPECS: tuple[ToolSpec, ...] = (
    # group "Organize"
    ToolSpec(
        "merge",
        "Merge PDF",
        "Organize",
        Family.B,
        lambda v: PDFService().merge(v.inputs, v.output),
        output_suffix="_merged",
        output_ext=".pdf",
        icon="\U0001f4d1",  # 📑 bookmark tabs — joining pages into one stack
    ),
    ToolSpec(
        "split",
        "Split PDF",
        "Organize",
        Family.C,
        lambda v: PDFService().split(v.source, v.output_dir, ranges=None),
        output_kind=OutputKind.DIRECTORY,
        icon="✂",  # ✂ scissors — cutting pages apart
    ),
    ToolSpec(
        "organize",
        "Organize PDF",
        "Organize",
        Family.E,
        lambda v: PDFService().organize(v.source, v.output, v.order),
        output_suffix="_organized",
        output_ext=".pdf",
        icon="\U0001f5c2",  # 🗂 card index dividers — reordering pages
    ),
    # group "Secure"
    ToolSpec(
        "protect",
        "Protect PDF",
        "Secure",
        Family.D,
        # An empty optional user_password (field left blank) becomes None,
        # matching PDFService.protect's "owner-only" contract.
        lambda v: PDFService().protect(
            v.source,
            v.output,
            v.secrets["owner_password"],
            v.secrets.get("user_password") or None,
        ),
        output_suffix="_protected",
        output_ext=".pdf",
        secret_fields=(
            SecretField("owner_password", "Owner password", True),
            SecretField("user_password", "User password (optional)", False),
        ),
        icon="\U0001f512",  # 🔒 locked padlock
    ),
    ToolSpec(
        "unlock",
        "Unlock PDF",
        "Secure",
        Family.D,
        lambda v: PDFService().unlock(v.source, v.output, v.secrets["password"]),
        output_suffix="_unlocked",
        output_ext=".pdf",
        secret_fields=(SecretField("password", "Password", True),),
        icon="\U0001f513",  # 🔓 open padlock
    ),
    # group "Convert"
    ToolSpec(
        "convertir",
        "Word to PDF",
        "Convert",
        Family.A,
        lambda v: ExportService().convertir(v.source, v.output),
        output_ext=".pdf",
        icon="\U0001f4c4",  # 📄 document — Word into PDF
    ),
    ToolSpec(
        "pdf_a_word",
        "PDF to Word",
        "Convert",
        Family.A,
        lambda v: ExportService().pdf_a_word(v.source, v.output),
        output_ext=".docx",
        icon="\U0001f4c3",  # 📃 page with curl — PDF into Word
    ),
    ToolSpec(
        "pdf_a_excel",
        "PDF to Excel",
        "Convert",
        Family.A,
        lambda v: ExportService().pdf_a_excel(v.source, v.output),
        output_ext=".xlsx",
        icon="\U0001f4ca",  # 📊 bar chart — PDF into Excel
    ),
    ToolSpec(
        "jpg_to_pdf",
        "JPG to PDF",
        "Convert",
        Family.B,
        lambda v: PDFService().jpg_to_pdf(v.inputs, v.output),
        output_ext=".pdf",
        icon="\U0001f5bc",  # 🖼 framed picture — JPG source into PDF
    ),
    ToolSpec(
        "compress",
        "Compress PDF",
        "Convert",
        Family.A,
        lambda v: PDFService().compress(v.source, v.output),
        output_suffix="_compressed",
        output_ext=".pdf",
        icon="\U0001f5dc",  # 🗜 clamp — compressing file size
    ),
    # group "Recognize"
    ToolSpec(
        "ocr",
        "OCR PDF",
        "Recognize",
        Family.A,
        lambda v: OCRService().ocr(v.source, v.output),
        output_suffix="_ocr",
        output_ext=".pdf",
        icon="\U0001f50d",  # 🔍 magnifying glass — reading text via OCR
    ),
    ToolSpec(
        "scan_to_pdf",
        "Scan to PDF",
        "Recognize",
        Family.B,
        lambda v: ScanService().scan_to_pdf(v.inputs, v.output),
        output_suffix="_scanned",
        output_ext=".pdf",
        icon="\U0001f4f7",  # 📷 camera — scanning images into a PDF
    ),
    # group "Edit"
    ToolSpec(
        "edit_pdf",
        "Edit PDF",
        "Edit",
        Family.F,
        _run_edit_pdf,
        output_suffix="_edited",
        output_ext=".pdf",
        icon="✏",  # ✏ pencil — editing text on a page
    ),
)

#: `tool_id` -> `ToolSpec` lookup, built once at import time. `MainWindow`
#: (PR3) uses this to resolve a sidebar click's `tool_id` into the spec a
#: new/cached `ToolView` needs (design §5).
SPEC_BY_ID: dict[str, ToolSpec] = {spec.tool_id: spec for spec in TOOL_SPECS}
