"""Live-Tk tests for `ToolView`'s spinner/Run/status-label lifecycle.

Narrow, deliberate addition (post-`50504bb` polish-pass review): this
project's original testing-strategy decision was to skip full `ToolView`
automation in favor of a manual QA checklist, but that decision predates
the polish pass touching `_enter_running`/`_on_success`/`_on_error`'s
widget `.configure()` calls directly — the exact methods this project's
own docs call the highest-risk surface in the whole `acrobat-tools-ui`
change (a regression here defeats the entire point of the shared
`ToolView` base class). This module adds the minimum coverage to catch a
regression in that state machine, without expanding into full
widget-rendering/visual testing (still deliberately out of scope).

Assertions are black-box (observed widget state, not the module's
private color constants) so they stay robust to a future palette change.

Uses the session-scoped `tk_root` fixture from `tests/conftest.py`
(shared with `test_ui_panels.py`) rather than defining its own —
Tkinter does not reliably support multiple `Tk()` roots per process.
"""

from __future__ import annotations

import time
from pathlib import Path

import customtkinter as ctk

from app.core.concurrency.task_runner import TaskRunner
from app.ui.registry import Family, ToolSpec
from app.ui.views.tool_view import ToolView

_POLL_TIMEOUT_S = 3.0


def _make_spec() -> ToolSpec:
    # `run` is never invoked in these tests — they drive `_enter_running`/
    # `_on_success`/`_on_error` directly rather than through `_on_run`, so
    # this is a placeholder to satisfy the (frozen) dataclass contract.
    return ToolSpec(
        tool_id="test_tool",
        label="Test Tool",
        group="Test",
        family=Family.A,
        run=lambda values: Path("out.pdf"),
        output_ext=".pdf",
    )


def _wait_until(root: ctk.CTk, condition) -> None:
    deadline = time.time() + _POLL_TIMEOUT_S
    while not condition() and time.time() < deadline:
        root.update()
        time.sleep(0.01)
    assert condition(), "ToolView did not reach the expected terminal state in time"


class TestEnterRunning:
    def test_disables_run_button_and_shows_spinner(self, tk_root: ctk.CTk) -> None:
        runner = TaskRunner(scheduler=tk_root.after, cancel_scheduled=tk_root.after_cancel)
        view = ToolView(tk_root, runner, _make_spec())

        view._enter_running()

        assert view.run_button.cget("state") == "disabled"
        assert view.spinner.winfo_manager() == "pack"
        runner.shutdown()

    def test_resets_a_stale_status_label_back_to_idle(self, tk_root: ctk.CTk) -> None:
        runner = TaskRunner(scheduler=tk_root.after, cancel_scheduled=tk_root.after_cancel)
        view = ToolView(tk_root, runner, _make_spec())
        idle_fg_color = view.status_label.cget("fg_color")
        view.status_label.configure(text="stale message", fg_color="red")

        view._enter_running()

        assert view.status_label.cget("text") == ""
        assert view.status_label.cget("fg_color") == idle_fg_color
        runner.shutdown()


class TestTerminalPaths:
    def test_success_re_enables_run_hides_spinner_and_paints_status(
        self, tk_root: ctk.CTk
    ) -> None:
        runner = TaskRunner(scheduler=tk_root.after, cancel_scheduled=tk_root.after_cancel)
        view = ToolView(tk_root, runner, _make_spec())
        idle_fg_color = view.status_label.cget("fg_color")

        view._enter_running()
        runner.submit(
            lambda: Path("out.pdf"), on_success=view._on_success, on_error=view._on_error
        )
        _wait_until(tk_root, lambda: view.run_button.cget("state") == "normal")

        assert view.spinner.winfo_manager() == ""
        assert view.status_label.cget("fg_color") != idle_fg_color
        assert "Saved to" in view.status_label.cget("text")
        runner.shutdown()

    def test_error_re_enables_run_hides_spinner_and_paints_status(
        self, tk_root: ctk.CTk
    ) -> None:
        runner = TaskRunner(scheduler=tk_root.after, cancel_scheduled=tk_root.after_cancel)
        view = ToolView(tk_root, runner, _make_spec())
        idle_fg_color = view.status_label.cget("fg_color")

        def _fail() -> Path:
            raise ValueError("boom")

        view._enter_running()
        runner.submit(_fail, on_success=view._on_success, on_error=view._on_error)
        _wait_until(tk_root, lambda: view.run_button.cget("state") == "normal")

        assert view.spinner.winfo_manager() == ""
        assert view.status_label.cget("fg_color") != idle_fg_color
        runner.shutdown()
