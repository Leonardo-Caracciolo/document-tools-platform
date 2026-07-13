# Acrobat Tools V2.0

A Windows desktop PDF utility suite built with Python and `customtkinter`, wrapping `pikepdf`, `pymupdf`, and related libraries behind a UI-agnostic service layer.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

## Features

The sidebar exposes 13 tool operations, defined as the single source of truth in `app/ui/registry.py` (`TOOL_SPECS`), grouped as follows:

### Organize
- **Merge PDF** — combine multiple PDFs into one file.
- **Split PDF** — split a PDF into per-range files in an output directory.
- **Organize PDF** — reorder a PDF's pages according to a supplied page order.

### Secure
- **Protect PDF** — add an owner password (and optional user password) to a PDF.
- **Unlock PDF** — remove password protection from a PDF given its password.

### Convert
- **Word to PDF** — convert a `.docx` to PDF (via a pluggable provider: local Word/COM or Azure Document Converter, selected by the `DOC_CONVERTER_PROVIDER` env var).
- **PDF to Word** — convert a PDF to `.docx` using `pdf2docx`.
- **PDF to Excel** — extract tables from a PDF into `.xlsx` using `pdfplumber` + `openpyxl`.
- **JPG to PDF** — assemble one or more JPG images into a single PDF.
- **Compress PDF** — recompress a PDF's embedded images (150 DPI / JPEG quality 75) to reduce file size.

### Recognize
- **OCR PDF** — add a searchable, invisible Spanish text layer to an image-only PDF (via a pluggable provider: local Tesseract or Azure OCR, selected by the `OCR_PROVIDER` env var).
- **Scan to PDF** — deskew and crop one or more photographed page images, assemble them into a PDF, then run OCR to produce a searchable output (composes `PDFService.jpg_to_pdf` + `OCRService.ocr`).

### Edit
- **Edit PDF** — a single mode-selector tool with four modes:
  - **Add text** — place text on a page at a preset anchor position or a clicked point.
  - **Highlight text** — highlight all matches of a search query on a page.
  - **Redact text** — permanently black out all matches of a search query on a page.
  - **Replace text** — click a text span in the page preview to select it, then replace it with new text. Optionally launches the standalone **Advanced Editor** (see below) for a larger, PySide6-based render/click/replace/save workflow in a separate window.

## Architecture

The codebase is layered so that PDF/OCR/conversion logic never depends on a UI toolkit:

- **`app/ui/`** — `customtkinter` UI: `MainWindow` (sidebar + content area), per-family input panels (`app/ui/widgets/panels.py`), and `ToolView`, which dispatches a `ToolSpec.run` callable to a shared `TaskRunner` so long-running work never blocks the UI thread.
- **`app/core/services/`** — the business logic layer: `PDFService` (pikepdf/pymupdf/img2pdf-based PDF and image operations), `ExportService` (Word<->PDF, PDF->Excel), `OCRService` (searchable-text-layer generation), and `ScanService` (deskew/crop + composition of `PDFService`/`OCRService`). These classes have **zero dependency on any UI toolkit** — verified by inspection, no `customtkinter`/`tkinter`/`PySide6` imports anywhere under `app/core/services/`. `PDFService` in particular is reused directly by both the Tkinter UI and the separate PySide6 Advanced Editor process.
- **`app/core/providers/`** — pluggable engine backends behind small interfaces (e.g. `TesseractOCRProvider`/`AzureOCRProvider`, `ComWordProvider`/`AzureDocConverterProvider`), selected via env-var-driven factories in their owning service.
- **`app/core/concurrency/task_runner.py`** — `TaskRunner` submits service calls to a `ThreadPoolExecutor` and marshals results back to the UI thread via a caller-supplied scheduler (`root.after`), so no worker thread ever touches a UI widget directly.
- **`app/infrastructure/`** — configuration loading (`AppConfig.load()`, env vars via `python-dotenv`) and logging setup.
- **`app/qt_editor/`** — the optional "Advanced Editor": a separate PySide6 process (`python -m app.qt_editor <pdf_path> --page N`), launched fire-and-forget as a subprocess from `EditPanel`'s Replace-text mode. It reuses `PDFService.render_page` for rendering and calls back into `PDFService` for replace/save. If PySide6 is not installed, the launch button degrades gracefully with an inline message instead of crashing the app.

## Requirements

- **Python 3.11+** (developed against `3.13`).
- **Windows** — the dependency list includes `pywin32` (COM automation for the local Word-to-PDF provider) and Windows-specific process handling; this application targets Windows.
- **Tesseract OCR** (external binary, only needed to use OCR / Scan to PDF locally): install via `winget install UB-Mannheim.TesseractOCR` or equivalent. The app locates the binary via, in order: the `TESSERACT_PATH` env var, `PATH`, or the typical install location.
  - `OCRService` hardcodes Spanish recognition (`lang="spa"`). The standard Windows Tesseract distribution ships only `eng`/`osd` language data — `spa.traineddata` must be downloaded separately from the [tessdata repo](https://github.com/tesseract-ocr/tessdata/raw/main/spa.traineddata) and placed in Tesseract's `tessdata` directory (or pointed to via `TESSDATA_PREFIX`). See `app/config/README.md` for details.

## Installation

```powershell
git clone https://github.com/Leonardo-Caracciolo/document-tools-platform.git
cd document-tools-platform
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Install the package in editable mode:

```powershell
pip install -e .
```

Install the dev tooling groups. This project's `[dependency-groups]` follows PEP 735, which requires `pip>=25.1` to install directly with `--group`:

```powershell
pip install --group lint
pip install --group test
```

On `pip<25.1`, install the tools manually instead (the package is already installed in editable mode above):

```powershell
pip install ruff pytest python-docx
```

The **Advanced Editor** is an optional feature and requires its own group/package:

```powershell
pip install --group qt
# or, on pip<25.1:
pip install "PySide6>=6.11,<7"
```

### Environment configuration

The app requires a `.env` file at the repository root (gitignored — you must create it yourself). At minimum:

```
LOG_LEVEL=INFO
```

Everything else (`OCR_PROVIDER`, `TESSERACT_PATH`, `DOC_CONVERTER_PROVIDER`, `SQLSERVER_HOST`/`SQLSERVER_DB`/`SQLSERVER_USER`/`SQLSERVER_PASSWORD`) is optional and defaults to unset — `AppConfig.load()` only fails fast on a missing `LOG_LEVEL`.

**Common pitfall:** on Windows, creating this file with PowerShell's `echo`/`>` redirection writes **UTF-16 with a BOM**, which breaks `python-dotenv`'s UTF-8 parser (the app will fail to start with a confusing "missing `LOG_LEVEL`" error even though the file looks correct). Use `Set-Content` with an explicit encoding instead:

```powershell
Set-Content -Path .env -Value "LOG_LEVEL=INFO" -Encoding ascii
```

## Usage

Run the application:

```powershell
python -m app.main
```

The Advanced Editor is normally launched from within the Edit PDF panel's Replace-text mode, but it can also be run standalone (useful when working on that module directly):

```powershell
python -m app.qt_editor path\to\file.pdf --page 1
```

## Testing

```powershell
python -m pytest tests/unit -q
```

As of this writing, `tests/unit` collects 453 tests. There is also a small `tests/integration` suite (4 tests) that exercises real external processes (Word/COM, Tesseract) and is not part of the default fast test loop:

```powershell
python -m pytest tests/integration -q
```

Lint:

```powershell
ruff check .
```

## Project Structure

```
app/
├── main.py                  # Entry point: loads config, starts the Tk event loop
├── core/
│   ├── services/             # UI-agnostic business logic (PDFService, ExportService, OCRService, ScanService)
│   ├── providers/             # Pluggable engine backends (Tesseract/Azure OCR, COM/Azure doc converter)
│   ├── concurrency/           # TaskRunner — off-UI-thread execution
│   └── exceptions.py          # Domain exceptions shared across services
├── infrastructure/
│   ├── config.py               # AppConfig.load() — typed env-var configuration
│   └── logger.py                # Logging setup
├── ui/
│   ├── main_window.py          # Sidebar + content area shell
│   ├── registry.py              # TOOL_SPECS — single source of truth for every tool
│   ├── views/                   # ToolView (dispatches a ToolSpec.run via TaskRunner)
│   └── widgets/                  # Per-family input panels, rows, PDF page preview
├── qt_editor/                # Optional PySide6 "Advanced Editor" subprocess
└── config/
    └── README.md               # Deployment notes (e.g. Tesseract Spanish language pack)

tests/
├── unit/                    # Fast, isolated tests (453 collected)
├── integration/              # Tests against real external processes (Word/COM, Tesseract)
└── fixtures/                  # Synthetic PDF/DOCX/image factories
```

## Contributing

- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `test:`, `chore:`, `docs:`), consistent with this repository's history.
- Run tests and lint before committing:

```powershell
python -m pytest tests/unit -q
ruff check .
```

## License

No license file is currently present in this repository.
