"""Standalone PySide6 "Advanced Editor" process.

Launched via `python -m app.qt_editor <pdf_path> [--page N]` as a
fire-and-forget child process from `app.ui.widgets.panels.EditPanel`
(replace_text mode only — see `sdd/qt-advanced-editor-slice1/design`).

Slice 1 is render+display only: one PDF page is rendered read-only via
the already-shipped `PDFService().render_page`. No editing, no save, no
IPC back to the parent process. This package has zero import-time
dependency on customtkinter (it does import `app.ui.errors.error_message`
to funnel through the same single exception-to-message resolver the
Tkinter surfaces use — that module itself has zero customtkinter
dependency, see its own docstring), and the rest of the application has
zero import-time dependency on PySide6 — the process boundary keeps the
two GUI toolkits fully isolated from each other.
"""

from __future__ import annotations
