"""Per-tool views package (one `ToolView` instance per PDF tool).

Re-exports `ToolView`, the shared cross-cutting chrome + lifecycle base
every tool's view is an instance of (design ADR-001/ADR-002) — per
`sdd/acrobat-tools-ui/design` §10's file layout.
"""

from __future__ import annotations

from app.ui.views.tool_view import ToolView

__all__ = ["ToolView"]
