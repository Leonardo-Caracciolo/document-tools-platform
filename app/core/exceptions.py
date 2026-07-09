"""Domain exceptions shared across the application.

`ConfigError` is raised by `app.infrastructure.config` when required
configuration is missing or invalid, so the application fails fast at
startup instead of continuing with an undefined value.
"""

from __future__ import annotations


class ConfigError(Exception):
    """Raised when required application configuration is missing or invalid."""
