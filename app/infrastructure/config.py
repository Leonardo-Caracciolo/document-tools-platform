"""Application configuration loaded from environment variables.

`AppConfig.load()` loads `.env` (via `python-dotenv`) into the process
environment — without overriding variables already set — and then builds
a frozen, typed configuration object from `os.environ`.

Only keys actually consumed by Sprint 0 code (`LOG_LEVEL`) are required
and validated fail-fast. Sprint 1+ provider-specific keys (`OCR_PROVIDER`,
`TESSERACT_PATH`, `DOC_CONVERTER_PROVIDER`, `SQLSERVER_*`) are optional and
default to `None` until the sprint that implements their provider marks
them required — no code consumes them yet, so failing fast on them here
would block startup for no functional reason.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from app.core.exceptions import ConfigError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOTENV_PATH = _REPO_ROOT / ".env"

#: Keys required today (fail-fast if missing).
REQUIRED_ENV_KEYS: tuple[str, ...] = ("LOG_LEVEL",)

#: Sprint 1+ provider keys — optional until their provider ships.
OPTIONAL_ENV_KEYS: tuple[str, ...] = (
    "OCR_PROVIDER",
    "TESSERACT_PATH",
    "DOC_CONVERTER_PROVIDER",
    "SQLSERVER_HOST",
    "SQLSERVER_DB",
    "SQLSERVER_USER",
    "SQLSERVER_PASSWORD",
)

#: Single source of truth for every env key this module reads — tests use
#: this to clear state instead of re-listing the keys, so a renamed or
#: added key can't silently drift out of sync between prod code and tests.
ALL_ENV_KEYS: tuple[str, ...] = REQUIRED_ENV_KEYS + OPTIONAL_ENV_KEYS


def _require(key: str) -> str:
    """Return `os.environ[key]` or raise `ConfigError` naming the key."""
    value = os.environ.get(key)
    if not value:
        raise ConfigError(f"Missing required environment variable: {key}")
    return value


@dataclass(frozen=True)
class OcrConfig:
    """OCR provider settings — Sprint 1+, optional until a provider ships."""

    provider: str | None = None
    tesseract_path: str | None = None


@dataclass(frozen=True)
class DocConverterConfig:
    """Document converter provider settings — Sprint 1+, optional for now."""

    provider: str | None = None


@dataclass(frozen=True)
class DbConfig:
    """SQL Server connection settings — Sprint 1+, optional for now."""

    host: str | None = None
    database: str | None = None
    user: str | None = None
    password: str | None = None


@dataclass(frozen=True)
class AppConfig:
    """Typed, immutable application configuration."""

    ocr: OcrConfig
    doc_converter: DocConverterConfig
    db: DbConfig
    log_level: str

    @classmethod
    def load(cls) -> "AppConfig":
        """Load `.env` into the environment and build a typed `AppConfig`.

        Raises:
            ConfigError: if a required key (currently only `LOG_LEVEL`) is
                missing from both the environment and `.env`.
        """
        load_dotenv(_DOTENV_PATH, override=False)

        log_level = _require("LOG_LEVEL")
        ocr = OcrConfig(
            provider=os.environ.get("OCR_PROVIDER"),
            tesseract_path=os.environ.get("TESSERACT_PATH"),
        )
        doc_converter = DocConverterConfig(
            provider=os.environ.get("DOC_CONVERTER_PROVIDER"),
        )
        db = DbConfig(
            host=os.environ.get("SQLSERVER_HOST"),
            database=os.environ.get("SQLSERVER_DB"),
            user=os.environ.get("SQLSERVER_USER"),
            password=os.environ.get("SQLSERVER_PASSWORD"),
        )
        return cls(ocr=ocr, doc_converter=doc_converter, db=db, log_level=log_level)
