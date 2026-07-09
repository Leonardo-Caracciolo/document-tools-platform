"""Tests for `app.infrastructure.config`."""

from __future__ import annotations

import pytest

from app.core.exceptions import ConfigError
from app.infrastructure.config import ALL_ENV_KEYS, AppConfig


def _clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ALL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_load_succeeds_with_valid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("OCR_PROVIDER", "tesseract")
    monkeypatch.setenv("TESSERACT_PATH", "C:/tools/tesseract.exe")
    monkeypatch.setenv("DOC_CONVERTER_PROVIDER", "libreoffice")
    monkeypatch.setenv("SQLSERVER_HOST", "localhost")
    monkeypatch.setenv("SQLSERVER_DB", "acrobat_tools")
    monkeypatch.setenv("SQLSERVER_USER", "sa")
    monkeypatch.setenv("SQLSERVER_PASSWORD", "secret")

    config = AppConfig.load()

    assert config.log_level == "DEBUG"
    assert config.ocr.provider == "tesseract"
    assert config.ocr.tesseract_path == "C:/tools/tesseract.exe"
    assert config.doc_converter.provider == "libreoffice"
    assert config.db.host == "localhost"
    assert config.db.database == "acrobat_tools"
    assert config.db.user == "sa"
    assert config.db.password == "secret"


def test_load_succeeds_when_only_required_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sprint 1+ provider keys are optional: no code consumes them yet."""
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    config = AppConfig.load()

    assert config.log_level == "INFO"
    assert config.ocr.provider is None
    assert config.ocr.tesseract_path is None
    assert config.doc_converter.provider is None
    assert config.db.host is None


def test_load_raises_config_error_when_required_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_config_env(monkeypatch)

    with pytest.raises(ConfigError) as exc_info:
        AppConfig.load()

    assert "LOG_LEVEL" in str(exc_info.value)


def test_config_dataclasses_are_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    config = AppConfig.load()

    with pytest.raises(Exception):  # noqa: B017, PT011 — FrozenInstanceError
        config.log_level = "DEBUG"  # type: ignore[misc]
