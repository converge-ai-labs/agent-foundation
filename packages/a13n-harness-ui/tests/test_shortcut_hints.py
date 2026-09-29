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
        assert "/notes" in hints
        assert "Enter send" not in hints and "Enter steer" not in hints
        assert len(hints.splitlines()) <= 2
        assert all(get_cwidth(row) <= width for row in hints.splitlines())
        if width >= 60:
            for shortcut in ("Alt+Enter newline", "Ctrl+O details", "F2 tasks"):
                assert shortcut in hints
            assert ("Ctrl+C cancel" if shell.busy else "Ctrl+C twice exit") in hints
            assert ("Esc select" if mouse else "Esc scroll") in hints
            if width >= 80:
                assert ("PgUp/PgDn scroll" if follow else "Ctrl+End latest") in hints
        else:
            assert "/help" in hints
            assert ("Esc select" if mouse else "Esc scroll") in hints
        if width == 160:
            assert len(hints.splitlines()) == 1
        header = "".join(text for _, text in shell._composer_header())
        if width < 60:
            assert ("Scroll" if mouse else "Select") in header
            assert get_cwidth(header) <= width
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
        assert "/notes" not in hints
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
    assert "Esc select" not in hints and "/notes" not in hints
    assert all(get_cwidth(row) <= width for row in hints.splitlines())
    shell.menu_handler = AsyncMock()
    assert "Esc back" in shell._hints()
    assert "Esc dismiss" not in shell._hints()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["chat", "selector", "history"])
@pytest.mark.parametrize("mouse", [True, False])
async def test_rendered_footer_reflows_on_resize_and_preserves_input(
    shell: CliShell, monkeypatch: pytest.MonkeyPatch, mode: str, mouse: bool
) -> None:
    shell.mouse = mouse
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
                    if mode == "chat":
                        assert "/notes" in "\n".join(rows[-len(expected) :])
                        if width < 60:
                            assert ("Scroll" if mouse else "Select") in "\n".join(rows)
                    if mode == "history":
                        assert "/notes" not in "\n".join(expected)
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


@pytest.mark.anyio
@pytest.mark.parametrize(("total", "omitted"), [(0, 0), (2, 0), (257, 256)])
async def test_note_count_uses_saved_total_and_shares_activity_row(
    shell: CliShell, monkeypatch, total, omitted
) -> None:
    from a13n_harness_ui.surfaces import NotePage, NoteView

    page = NotePage(
        notes=tuple(NoteView(key=f"note-{i}", value="body") for i in range(total - omitted)),
        total=total,
        omitted=omitted,
    )
    shell.backend = SimpleNamespace(
        thread_id="thread-one", app=SimpleNamespace(thread_notes=AsyncMock(return_value=page))
    )
    shell._activity_thread = "thread-one"
    shell._subagent_total = 3
    assert "Notes" not in shell._activity_hint()
    await shell._load_notes()
    shell.renderer.gap = True
    hint = shell._activity_hint()
    assert hint.startswith(f"Notes {total} · /notes")
    assert "Subagents 3 total · /subagents" in hint
    assert "Background 0+ observed · /ps" in hint
    for width in (24, 40, 60, 80, 160):
        monkeypatch.setattr(shell.app.output, "get_size", lambda width=width: Size(rows=24, columns=width))
        text = "".join(text for _, text in shell._activity_text())
        assert text.startswith(f"Notes {total}")
        assert "\n" not in text and get_cwidth(text) <= width
        if width >= 60:
            assert "Subagents 3" in text and "Background 0+" in text
        with set_app(shell.app):
            shell.app.render_counter += 1
            shell.app.renderer.render(shell.app, shell.app.layout)
            screen = shell.app.renderer._last_screen
            assert screen is not None
            rows = ["".join(screen.data_buffer[y][x].char for x in range(width)).rstrip() for y in range(24)]
            assert text in rows
    shell.backend.thread_id = "thread-two"
    assert "Notes" not in shell._activity_hint()


@pytest.mark.anyio
async def test_note_count_refreshes_and_discards_cross_thread_snapshot(shell: CliShell) -> None:
    import asyncio

    from a13n_harness_ui.surfaces import NotePage

    query = AsyncMock(return_value=NotePage(total=4, omitted=4))
    shell.backend = SimpleNamespace(thread_id="thread-one", app=SimpleNamespace(thread_notes=query))
    await shell._load_notes()
    assert shell.renderer.note_count == 4
    query.return_value = NotePage()
    await shell._load_notes()
    assert shell.renderer.note_count == 0
    gate = asyncio.Event()
    started = asyncio.Event()

    async def delayed(**kwargs):
        started.set()
        await gate.wait()
        return NotePage(total=7, omitted=7)

    query.side_effect = delayed
    pending = asyncio.create_task(shell._load_notes())
    await started.wait()
    shell.backend.thread_id = "thread-two"
    gate.set()
    await pending
    assert "Notes" not in shell._activity_hint()
    assert shell.renderer.note_count == 0


@pytest.mark.parametrize("width", [24, 40, 59, 60, 80, 100, 160])
@pytest.mark.parametrize("fast", ["default", "off", "on"])
@pytest.mark.parametrize("tokens", [None, 0, 12345, 1234567])
@pytest.mark.anyio
async def test_status_prioritizes_fast_and_cumulative_tokens_on_narrow_screens(
    shell: CliShell, monkeypatch, width, fast, tokens
) -> None:
    from a13n_harness.usage import BoundedRequestUsage

    shell.status.state = "ready"
    shell.status.fast = fast
    shell.status.context_tokens = 1200
    shell.status.context_window = 350000
    if tokens is not None:
        shell.status.usage = BoundedRequestUsage(input_tokens=tokens, cache_read_tokens=tokens // 2)
    monkeypatch.setattr(shell.app.output, "get_size", lambda: Size(rows=24, columns=width))
    line = shell.status.line(width)
    assert get_cwidth(line) <= width
    assert ("Fast" in line) == (fast == "on")
    assert ("tok " if width < 60 else "tokens ") in line
    if tokens is None:
        assert "--" in line
    elif tokens == 0:
        assert " 0" in line
    else:
        assert ("12.3K" if tokens == 12345 else "1.2M") in line
    if width >= 80:
        assert "ctx 1,200 (0%)" in line
    with set_app(shell.app):
        shell.app.render_counter += 1
        shell.app.renderer.render(shell.app, shell.app.layout)
        screen = shell.app.renderer._last_screen
        assert screen is not None
        rows = ["".join(screen.data_buffer[y][x].char for x in range(width)).rstrip() for y in range(24)]
        assert line.rstrip() in rows
    await shell.app.cancel_and_wait_for_background_tasks()
