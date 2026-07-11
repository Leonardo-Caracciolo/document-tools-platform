"""Application entrypoint (placement only) — no PDF/OCR/converter logic.

Wires configuration, logging, and the main window together, then starts
the Tk event loop. Sprint 1+ tool views and services attach to
`MainWindow`; this module contains no PDF manipulation code.
"""

from __future__ import annotations

import customtkinter

from app.infrastructure.config import AppConfig
from app.infrastructure.logger import configure_logging, get_logger
from app.ui.main_window import MainWindow

_logger = get_logger(__name__)


def main() -> None:
    """Load configuration, configure logging, and run the main window."""
    config = AppConfig.load()
    configure_logging(config.log_level)
    _logger.info("Starting Acrobat Tools")

    # Global appearance calls (customtkinter requirement) must happen
    # BEFORE any widget is constructed, so these precede MainWindow().
    customtkinter.set_appearance_mode("system")
    customtkinter.set_default_color_theme("dark-blue")

    window = MainWindow()
    window.mainloop()


if __name__ == "__main__":
    main()
