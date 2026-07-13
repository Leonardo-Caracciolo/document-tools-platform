"""`-m` entry point for the standalone "Advanced Editor" process.

Thin shell only: parse args -> `QApplication` -> build
`AdvancedEditorWindow` -> `app.exec()`. All render/display logic lives in
`app.qt_editor.editor_window` (one concern per module).

Usage: `python -m app.qt_editor <pdf_path> [--page N]`
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.qt_editor.editor_window import AdvancedEditorWindow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.qt_editor")
    parser.add_argument("pdf_path")
    parser.add_argument("--page", type=int, default=1)
    args = parser.parse_args(argv)

    # Curated argv so Qt never tries to parse our own --page argument.
    app = QApplication([sys.argv[0]])
    window = AdvancedEditorWindow(Path(args.pdf_path), args.page)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
