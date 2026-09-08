"""Shortcut hints fit whole labels and reserve their actual rendered height."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.history import HistoryBrowser
from a13n_harness_ui.interactive.selection import Choice, Selection
from a13n_harness_ui.interactive.shell import CliShell
from prompt_toolkit.application import create_app_session
from prompt_toolkit.application.current import set_app
from prompt_toolkit.completion import Completion
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth


@pytest.fixture
def shell() -> Iterator[CliShell]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        yield shell
        if shell.history_browser is not None:
            shell.history_browser.close()
        shell.renderer.transcript.close()


@pytest.mark.parametrize("width", [24, 40, 59, 60, 80, 100, 160])
@pytest.mark.parametrize("state", ["preparing", "idle", "working", "steering"])
def test_chat_hints_prioritize_history_without_repeating_submission(
    shell: CliShell, monkeypatch: pytest.MonkeyPatch, width: int, state: str
) -> None:
    monkeypatch.setattr(shell.app.output, "get_size", lambda: Size(rows=24, columns=width))
    shell.ready = state != "preparing"
    monkeypatch.setattr(CliShell, "busy", property(lambda self: state in {"working", "steering"}))
    monkeypatch.setattr(CliShell, "can_steer", property(lambda self: state == "steering"))
    for mouse, follow in ((True, True), (False, False)):
        shell.mouse, shell.view.follow = mouse, follow
        hints = shell._hints()
        assert hints.startswith(" Ctrl+T history")
        assert "Enter send" not in hints and "Enter steer" not in hints
        assert len(hints.splitlines()) <= 2
        assert all(get_cwidth(row) <= width for row in hints.splitlines())
        if width >= 60:
            for shortcut in ("Alt+Enter newline", "Ctrl+O details", "F2 tasks"):
                assert shortcut in hints
            assert ("Ctrl+C cancel" if shell.busy else "Ctrl+C twice exit") in hints
            assert ("Esc select" if mouse else "Esc scroll") in hints
            assert ("PgUp/PgDn scroll" if follow else "Ctrl+End latest") in hints
        else:
            assert "/help" in hints
        if width == 160:
            assert len(hints.splitlines()) == 1
        header = "".join(text for _, text in shell._composer_header())
        if width >= 60:
            expected = {
                "preparing": "draft only · preparing",
                "idle": "Enter to send",
                "working": "draft only · working",
                "steering": "Enter to add guidance",
            }[state]
            assert expected in header


@pytest.mark.parametrize("width", [24, 40, 60, 80, 100])
def test_modal_hints_describe_current_focus_and_completion(
    shell: CliShell, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    monkeypatch.setattr(shell.app.output, "get_size", lambda: Size(rows=24, columns=width))
    shell.selection = Selection((Choice("yes", "Yes"),))
    for focused in (True, False):
        shell.selector_focused = focused
        hints = shell._hints()
        assert "Enter confirm" in hints and "Esc back" in hints
        assert "Ctrl+T history" not in hints and "Enter send" not in hints
        assert all(get_cwidth(row) <= width for row in hints.splitlines())
        if width >= 60:
            assert "Ctrl+Space focus" in hints
            assert ("Menu" if focused else "Composer") in hints
            assert ("↑↓ choose" if focused else "↑↓ edit") in hints
    shell.selection = None
    shell.interaction = SimpleNamespace()
    assert "Ctrl+Space" not in shell._hints()
    shell.interaction = None
    shell.composer.buffer._set_completions([Completion("/help")])
    hints = shell._hints()
    assert "Enter complete" in hints and "Esc dismiss" in hints
    assert "Esc select" not in hints
    assert all(get_cwidth(row) <= width for row in hints.splitlines())
    shell.menu_handler = AsyncMock()
    assert "Esc back" in shell._hints()
    assert "Esc dismiss" not in shell._hints()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["chat", "selector", "history"])
async def test_rendered_footer_reflows_on_resize_and_preserves_input(
    shell: CliShell, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    if mode == "selector":
        shell.selection = Selection((Choice("yes", "Yes"),))
    elif mode == "history":
        browser = HistoryBrowser(shell.status, AsyncMock(), shell.app.invalidate)
        shell.history_browser = browser
        shell.history_window.content = browser.view
        shell.app.layout.focus(browser.view)
    try:
        with set_app(shell.app):
            for width, height in ((160, 24), (60, 24), (80, 24), (100, 24), (24, 24), (60, 12), (60, 6), (160, 24)):
                size = Size(rows=height, columns=width)
                monkeypatch.setattr(shell.app.output, "get_size", lambda size=size: size)
                shell.app.render_counter += 1
                shell.app.renderer.render(shell.app, shell.app.layout)
                screen = shell.app.renderer._last_screen
                assert screen is not None
                rows = ["".join(screen.data_buffer[y][x].char for x in range(width)).rstrip() for y in range(height)]
                expected = shell._hints().splitlines()
                if mode == "history" or height >= 12:
                    # String checks alone miss the original fixed-height clipping bug.
                    assert rows[-len(expected) :] == expected
                    assert len(expected) <= (2 if height >= 12 else 1)
                    if mode == "history":
                        assert "Ctrl+T/q close" in "\n".join(expected)
                        if width >= 60 and height >= 12:
                            assert "Home page top" in "\n".join(expected)
                            assert "End latest" in "\n".join(expected)
                            assert "Ctrl+O details" in "\n".join(expected)
                else:
                    assert "Ctrl+T history" not in "\n".join(rows)
                if mode != "history":
                    assert any(row.startswith(" >") for row in rows)
                    assert shell.composer.window.render_info is not None
                    assert shell.composer.window.render_info.window_height >= 1
    finally:
        await shell.app.cancel_and_wait_for_background_tasks()
