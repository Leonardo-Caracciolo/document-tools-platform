"""Tests for `app.infrastructure.logger`."""

from __future__ import annotations

import logging

import pytest

from app.infrastructure.logger import configure_logging, get_logger


def test_get_logger_returns_named_logger() -> None:
    logger = get_logger("acrobat_tools.tests.logger_name")

    assert logger.name == "acrobat_tools.tests.logger_name"


def test_configure_logging_installs_structured_formatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    handler = configure_logging("INFO")

    record = logging.LogRecord(
        name="acrobat_tools.tests.format",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    formatted = handler.formatter.format(record)

    assert "INFO" in formatted
    assert "acrobat_tools.tests.format" in formatted
    assert "hello world" in formatted


def test_configure_logging_does_not_duplicate_handlers() -> None:
    root = logging.getLogger()
    configure_logging("INFO")
    handlers_after_first = len(root.handlers)

    configure_logging("INFO")
    handlers_after_second = len(root.handlers)

    assert handlers_after_first == handlers_after_second


def test_configure_logging_suppresses_debug_when_level_is_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_logging("INFO")
    logger = get_logger("acrobat_tools.tests.suppress")

    logger.debug("debug message should be suppressed")
    logger.info("info message should appear")

    messages = [
        record.message for record in caplog.records if record.name == logger.name
    ]

    assert "debug message should be suppressed" not in messages
    assert "info message should appear" in messages


def test_configure_logging_allows_debug_when_level_is_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_logging("DEBUG")
    logger = get_logger("acrobat_tools.tests.allow_debug")

    logger.debug("debug message should appear")

    messages = [
        record.message for record in caplog.records if record.name == logger.name
    ]

    assert "debug message should appear" in messages
