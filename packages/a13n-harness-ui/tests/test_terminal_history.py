"""Resume hydrates a bounded recent tail, independently of durable history."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.history import restore_transcript
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.surfaces import TranscriptEntry, TranscriptPage, TranscriptPart
from a13n_stream_protocol import ContentMetadata
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from .terminal_display_fixtures import present_text


def _source(renderer: StreamRenderer) -> str:
    return "\n".join(block.source for block in renderer.transcript.blocks.values())


def _page(*parts: TranscriptPart, next_cursor: str | None = None) -> TranscriptPage:
    return TranscriptPage(
        entries=(TranscriptEntry(position=0, message_kind="response", parts=parts),), total=1, next_cursor=next_cursor
    )


def test_restore_preserves_roles_and_omits_internal_guidance() -> None:
    renderer = StreamRenderer(Status())
    restore_transcript(
        renderer,
        _page(
            TranscriptPart(kind="system", text="hidden system"),
            TranscriptPart(kind="user", text="hidden guidance", metadata=ContentMetadata(display=False)),
            TranscriptPart(kind="user", text="my question"),
            TranscriptPart(kind="thinking", text="visible reasoning"),
            TranscriptPart(kind="assistant", text="**my answer**"),
        ),
    )
    blocks = list(renderer.transcript.blocks.values())
    assert [block.kind for block in blocks] == ["user", "thinking", "text"]
    assert "> my question" in blocks[0].source
    assert blocks[2].markdown
    assert "hidden" not in _source(renderer)
    renderer.transcript.close()


@pytest.mark.parametrize("large", [False, True])
def test_recent_history_limits_parts_bytes_and_marks_omissions(large: bool) -> None:
    renderer = StreamRenderer(Status())
    parts = tuple(
        TranscriptPart(kind="assistant", text=("x" * 60000 if large else "short") + f" END {i}") for i in range(400)
    )
    restore_transcript(renderer, _page(*parts, next_cursor="older"))
    assert renderer.transcript.evicted
    assert len(renderer.transcript.blocks) <= 101
    assert renderer.transcript.source_bytes < 257 * 1024
    assert "END 399" in _source(renderer)
    assert "END 0\n" not in _source(renderer)
    assert "Ctrl+T" in _source(renderer)
    renderer.transcript.render(80)
    renderer.transcript.render(42)
    renderer.transcript.close()


def test_restoring_replaces_previous_screen_and_consumes_page_once() -> None:
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.emit("old session output")
        shell.backend = SimpleNamespace(
            resumed_transcript=_page(TranscriptPart(kind="assistant", text="resumed answer"))
        )
        shell._restore_resumed_history()
        assert "resumed answer" in _source(shell.renderer)
        assert "old session" not in _source(shell.renderer)
        assert shell.view.transcript is shell.renderer.transcript
        assert shell.backend.resumed_transcript is None
        shell._restore_resumed_history()
        assert _source(shell.renderer).count("resumed answer") == 1
        shell.renderer.transcript.close()


def test_many_live_turns_evict_old_display_but_keep_newest_turn() -> None:
    renderer = StreamRenderer(Status())
    for turn in range(800):
        renderer.local_input(str(turn), f"question {turn}")
        present_text(renderer, "", message_id=str(turn))
        present_text(renderer, f"answer {turn}\n" + "x" * 3000, message_id=str(turn))
        present_text(renderer, status="succeeded", message_id=str(turn))
    assert renderer.transcript.evicted
    assert len(renderer.transcript.blocks) <= 500
    assert renderer.transcript.source_bytes <= 2 * 1024 * 1024
    assert len(renderer._local_inputs) <= 128
    assert len(renderer._display_rows) <= 1024
    assert "question 0\n" not in _source(renderer)
    assert "answer 799" in _source(renderer)
    renderer.transcript.close()


@pytest.mark.anyio
async def test_browser_pages_without_accumulating_or_replaying_messages() -> None:
    from a13n_harness_ui.interactive.history import HistoryBrowser

    calls = []

    async def load(cursor, continuation):
        calls.append((cursor, continuation))
        number = int(cursor or "0")
        return _page(TranscriptPart(kind="assistant", text=f"page {number}"), next_cursor=str(number + 1))

    browser = HistoryBrowser(Status(), load, lambda: None)
    try:
        await browser.navigate()
        for number in range(1, 8):
            await browser.navigate(-1)
            assert _source(browser.renderer).endswith(f"page {number}")
            assert len(browser.renderer.transcript.blocks) == 2
        await browser.navigate(1)
        assert _source(browser.renderer).endswith("page 6")
        assert not browser.view.follow
        await browser.navigate()
        assert browser.cursors == [None] and browser.index == 0
        assert _source(browser.renderer).endswith("page 0")
    finally:
        browser.close()


@pytest.mark.anyio
async def test_browser_close_during_load_discards_stale_response() -> None:
    from a13n_harness_ui.interactive.history import HistoryBrowser

    release = asyncio.Event()
    started = asyncio.Event()

    async def load(cursor, continuation):
        started.set()
        await release.wait()
        return _page(TranscriptPart(kind="assistant", text="late response"))

    browser = HistoryBrowser(Status(), load, lambda: None)
    task = asyncio.create_task(browser.navigate())
    await started.wait()
    browser.close()
    release.set()
    await task
    assert browser.page is None


@pytest.mark.anyio
async def test_ctrl_t_viewer_preserves_draft_and_live_output() -> None:
    from unittest.mock import AsyncMock

    async def until(predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0.01)

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace(
            thread_id="thread-one",
            app=SimpleNamespace(
                get_thread_transcript=AsyncMock(
                    return_value=_page(TranscriptPart(kind="assistant", text="saved answer"))
                ),
            ),
        )
        shell.emit("live output")
        shell.composer.text = "unfinished draft"
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await until(lambda: shell.app.is_running)
            pipe.send_text("\x14")
            await until(lambda: shell.history_browser is not None and shell.history_browser.page is not None)
            assert "saved answer" in _source(shell.history_browser.renderer)
            shell.emit("incoming live output")
            assert "incoming" not in _source(shell.history_browser.renderer)
            pipe.send_text("q")
            await until(lambda: shell.history_browser is None)
            assert shell.composer.text == "unfinished draft"
            assert "incoming live output" in _source(shell.renderer)
            assert "saved answer" not in _source(shell.renderer)
        finally:
            shell.close_history()
            shell.app.exit()
            await terminal
            shell.renderer.transcript.close()
