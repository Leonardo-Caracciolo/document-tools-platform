"""Structured logging configuration for the application.

`configure_logging(level)` installs one structured handler on the root
logger (timestamp, level, logger/module name, message). Set the
`LOG_FORMAT` environment variable to `json` to switch to single-line JSON
records instead of plain text; any other value (or unset) keeps the plain
text format.

`get_logger(name)` is a thin factory around `logging.getLogger` so call
sites depend on this module instead of importing `logging` directly.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

_PLAIN_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

_configured_handler: logging.Handler | None = None


class _JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_formatter() -> logging.Formatter:
    if os.environ.get("LOG_FORMAT", "").strip().lower() == "json":
        return _JsonFormatter()
    return logging.Formatter(_PLAIN_FORMAT, datefmt=_DATE_FORMAT)


def configure_logging(level: str = "INFO") -> logging.Handler:
    """Configure the root logger with one structured handler.

    Safe to call more than once: only the handler this function
    previously installed is replaced, so other handlers (e.g. pytest's
    `caplog` capture handler) are left untouched and output is never
    duplicated across repeated calls.

    Returns:
        The `logging.Handler` that was installed.
    """
    global _configured_handler

    root = logging.getLogger()
    root.setLevel(level)

    if _configured_handler is not None and _configured_handler in root.handlers:
        root.removeHandler(_configured_handler)

    handler = logging.StreamHandler()
    handler.setFormatter(_build_formatter())
    root.addHandler(handler)
    _configured_handler = handler
    return handler


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call `configure_logging()` beforehand."""
    return logging.getLogger(name)
