"""Shared cross-cutting chrome + lifecycle for every tool view.

`sdd/acrobat-tools-ui/design` §3/§7 — ONE concrete `ToolView(ctk.CTkFrame)`
instance is created per `ToolSpec` (design ADR-001/ADR-002): the base owns
ALL shared chrome (title, panel mount, Run button, indeterminate spinner,
status label) and the ENTIRE `TaskRunner` submit/spinner/error lifecycle,
so per-tool "views" are pure data (`ToolSpec`), not per-tool subclasses.

Spinner symmetry (design ADR-007) is the single most important
correctness property here: `_exit_running()` is called by BOTH
`_on_success` and `_on_error` as their first action, so the Run button and
spinner can never desync from whether a task is actually in flight.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk

from app.core.concurrency.task_runner import TaskRunner
from app.ui.errors import error_message
from app.ui.registry import Family, ToolSpec
from app.ui.widgets.panels import (
    InputPanel,
    MultiInSingleOutPanel,
    OrderPanel,
    SecretPanel,
    SingleInDirOutPanel,
    SingleInSingleOutPanel,
)

#: `Family` -> panel factory, each returning a constructed `InputPanel`
#: mounted under `master`. Keeps `ToolView.__init__` free of per-family
#: branching beyond this one dispatch table (design §3/§4).
_PANEL_FACTORIES: dict[Family, Callable[[ctk.CTkBaseClass, ToolSpec], InputPanel]] = {
    Family.A: lambda master, spec: SingleInSingleOutPanel(
        master, output_suffix=spec.output_suffix, output_ext=spec.output_ext
    ),
    Family.B: lambda master, spec: MultiInSingleOutPanel(
        master, output_suffix=spec.output_suffix, output_ext=spec.output_ext
    ),
    Family.C: lambda master, spec: SingleInDirOutPanel(master),
    Family.D: lambda master, spec: SecretPanel(
        master,
        spec.secret_fields,
        output_suffix=spec.output_suffix,
        output_ext=spec.output_ext,
    ),
    Family.E: lambda master, spec: OrderPanel(
        master, output_suffix=spec.output_suffix, output_ext=spec.output_ext
    ),
}


#: Status "pill" colors (cosmetic only, design polish pass) — idle is
#: transparent (no background) so `status_label` reserves the same
#: vertical space at rest as when a message is showing, and the layout
#: never jumps when `_on_success`/`_show_error` set the text. Success/
#: error tints are (light-mode, dark-mode) pairs, matching customtkinter's
#: color-tuple convention.
_STATUS_IDLE_FG_COLOR = "transparent"
_STATUS_SUCCESS_FG_COLOR = ("#DCFCE7", "#14532D")
_STATUS_SUCCESS_TEXT_COLOR = ("#15803D", "#86EFAC")
_STATUS_ERROR_FG_COLOR = ("#FEE2E2", "#7F1D1D")
_STATUS_ERROR_TEXT_COLOR = ("#B91C1C", "#FCA5A5")


def format_success_message(result: Path | list[Path]) -> str:
    """Render a terminal success `result` as a one-line status message.

    Extracted as a pure function (no Tk dependency, independently
    testable) per design §3: a single `Path` -> "Saved to: {path}"; a
    `list[Path]` (only `split` returns this shape) -> "Saved N files to:
    {parent dir}".
    """
    if isinstance(result, list):
        parent = result[0].parent if result else ""
        return f"Saved {len(result)} files to: {parent}"
    return f"Saved to: {result}"


class ToolView(ctk.CTkFrame):
    """One instance per `ToolSpec` (design ADR-002) — chrome + lifecycle only.

    Per-tool behavior is entirely data-driven via `spec`: `spec.family`
    selects the mounted `InputPanel` subclass (`_PANEL_FACTORIES`), and
    `spec.run` is the off-thread service call `TaskRunner.submit` invokes.
    No per-tool subclass of `ToolView` is needed.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        task_runner: TaskRunner,
        spec: ToolSpec,
    ) -> None:
        super().__init__(master)
        self.task_runner = task_runner
        self.spec = spec

        self.title_label = ctk.CTkLabel(
            self, text=spec.label, font=ctk.CTkFont(size=20, weight="bold")
        )
        self.title_label.pack(anchor="w", padx=20, pady=(20, 12))

        # Card wrapper — a subtly different fg_color than the page
        # background so each tool's form reads as one distinct unit
        # (cosmetic only; the panel factory dispatch/behavior is unchanged).
        self.panel_card = ctk.CTkFrame(self, corner_radius=10, fg_color=("gray92", "gray17"))
        self.panel_card.pack(fill="x", padx=20, pady=(0, 20))

        self.panel: InputPanel = _PANEL_FACTORIES[spec.family](self.panel_card, spec)
        self.panel.pack(fill="x", padx=18, pady=18)

        self.run_button = ctk.CTkButton(
            self,
            text="Run",
            command=self._on_run,
            width=140,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.run_button.pack(anchor="w", padx=20, pady=(0, 12))

        # Indeterminate mode confirmed empirically (design §9 item 1). Not
        # packed here — hidden until `_enter_running()` makes it visible.
        self.spinner = ctk.CTkProgressBar(self)
        self.spinner.configure(mode="indeterminate")

        # Fixed height + always-packed (never pack_forget'd) so this row
        # reserves the same vertical space whether idle or showing a
        # success/error pill — the layout never jumps.
        self.status_label = ctk.CTkLabel(
            self, text="", height=32, corner_radius=8, fg_color=_STATUS_IDLE_FG_COLOR
        )
        self.status_label.pack(anchor="w", fill="x", padx=20, pady=(0, 20))

    def _on_run(self) -> None:
        """Run-button entry point (design §7).

        `panel.collect()` may raise `EntradaInvalidaError` for ANY family
        (design ADR-004) — that failure is handled entirely here, before
        `_enter_running()`, so the spinner never starts and Run never
        disables for a synchronous local-validation failure.
        """
        try:
            values = self.panel.collect()
        except Exception as exc:  # noqa: BLE001 - every collect() failure must resolve through the same error map, never a raw traceback
            self._show_error(exc)
            return

        self._enter_running()
        try:
            self.task_runner.submit(
                self.spec.run,
                values,
                on_success=self._on_success,
                on_error=self._on_error,
            )
        except Exception as exc:  # noqa: BLE001 - submit() can raise RuntimeError synchronously (wrong thread, after shutdown); must not leave the spinner running or Run disabled
            self._exit_running()
            self._show_error(exc)

    def _enter_running(self) -> None:
        """Disable Run, clear status, show + start the indeterminate spinner."""
        self.run_button.configure(state="disabled")
        self.status_label.configure(text="", fg_color=_STATUS_IDLE_FG_COLOR)
        self.spinner.pack(fill="x", padx=20, pady=(0, 12))
        self.spinner.start()

    def _exit_running(self) -> None:
        """Symmetric teardown (design ADR-007) — called by BOTH terminal paths.

        Stops + hides the spinner and re-enables Run. This is the ONLY
        place either happens, and it runs as the first action in both
        `_on_success` and `_on_error`, so a task in flight can never end
        without Run/spinner being restored to their idle state.
        """
        self.spinner.stop()
        self.spinner.pack_forget()
        self.run_button.configure(state="normal")

    def _on_success(self, result: Path | list[Path]) -> None:
        """UI-thread success callback passed to `TaskRunner.submit`."""
        self._exit_running()
        self.status_label.configure(
            text=format_success_message(result),
            text_color=_STATUS_SUCCESS_TEXT_COLOR,
            fg_color=_STATUS_SUCCESS_FG_COLOR,
        )

    def _on_error(self, exc: Exception) -> None:
        """UI-thread error callback passed to `TaskRunner.submit`."""
        self._exit_running()
        self._show_error(exc)

    def _show_error(self, exc: Exception) -> None:
        """Resolve `exc` via the shared error map and paint the status label.

        Never shows a raw traceback (spec's "Exception-to-Message
        Mapping" requirement) — `error_message()` is the single resolver
        for both this synchronous local-guard path and the async
        `_on_error` path.
        """
        self.status_label.configure(
            text=error_message(exc),
            text_color=_STATUS_ERROR_TEXT_COLOR,
            fg_color=_STATUS_ERROR_FG_COLOR,
        )
