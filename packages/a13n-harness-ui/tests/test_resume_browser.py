"""Session browsing stays detached until explicit, successful confirmation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.conversation import ConversationExcerpt
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.surfaces import (
    RootActivityState,
    ThreadPage,
    ThreadSummary,
    TranscriptEntry,
    TranscriptPage,
    TranscriptPart,
)
from a13n_harness_ui.thread_files import AttachmentUpload
from prompt_toolkit.application import create_app_session
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth


def _thread(number: int, *, project: str | None = "project-local") -> ThreadSummary:
    return ThreadSummary.model_validate(
        dict(
            thread_id=f"thread-{number}",
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
            updated_at=datetime(2026, 9, 1, tzinfo=UTC),
            activity_at=datetime(2026, 9, 1, tzinfo=UTC),
            metadata_version=1,
            title=None,
            excerpt=ConversationExcerpt(
                first_input=f"First input {number}",
                latest_input=f"Latest input {number}",
                latest_reply=f"Answer {number}",
                reply_kind="final",
            ),
            archived=False,
            configuration=dict(
                version=1,
                project_id=project,
                agent_source=dict(kind="agent", id="agent-test"),
                environment_profile_id="environment-native",
            ),
            continuation_state="selected",
            root_activity=dict(state=RootActivityState.inactive),
        )
    )


def _backend():
    return SimpleNamespace(
        thread_id="thread-current",
        directory="/work",
        resumed_transcript=None,
        app=SimpleNamespace(
            current_configuration=AsyncMock(
                return_value=SimpleNamespace(
                    projects={
                        "project-local": SimpleNamespace(roots=[SimpleNamespace(path="/work")]),
                        "project-other": SimpleNamespace(roots=[SimpleNamespace(path="/elsewhere")]),
                    }
                )
            ),
            cwd_project_ids=AsyncMock(return_value={"project-local"}),
            get_thread_transcript=AsyncMock(
                return_value=TranscriptPage(
                    entries=(
                        TranscriptEntry(
                            position=0,
                            message_kind="response",
                            parts=(TranscriptPart(kind="assistant", text="Retained answer"),),
                        ),
                    ),
                    total=1,
                )
            ),
            update_thread_metadata=AsyncMock(),
            thread_tasks=AsyncMock(return_value=[]),
        ),
        resume_sessions=AsyncMock(
            return_value=ThreadPage(threads=(_thread(1), _thread(2)), total=3, next_cursor="page2")
        ),
        resume=AsyncMock(side_effect=ValueError("Session is already running")),
        interaction=AsyncMock(return_value=None),
        skill_catalog=AsyncMock(return_value=None),
    )


async def _until(predicate) -> None:
    async with asyncio.timeout(4):
        while not predicate():
            await asyncio.sleep(0.01)


def _source(shell: CliShell) -> str:
    return "\n".join(block.source for block in shell.renderer.transcript.blocks.values())


@pytest.mark.anyio
async def test_keyboard_search_preview_history_rename_and_cancel_preserve_draft(monkeypatch) -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = backend = _backend()
        monkeypatch.setattr(shell, "_load_notes", AsyncMock())
        shell.images = (AttachmentUpload("picture.png", b"fixture", "image/png"),)
        marker = shell.pastes.insert("large paste " * 200)
        shell.composer.buffer.document = draft = Document("draft " + marker, cursor_position=2)
        shell.emit("Original live conversation")
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await _until(lambda: shell.app.is_running)
            await shell.command(shell.registry.parse("/resume"))
            await _until(lambda: shell.resume_browser is not None and shell.resume_browser.selected is not None)
            browser = shell.resume_browser
            assert "First input 1" in str(browser.rows())
            assert "Latest input 1" in browser.preview() and "Answer 1" in browser.preview()
            backend.app.get_thread_transcript.assert_not_called()
            pipe.send_text("\x1b[B")
            await _until(lambda: browser.selected.thread_id == "thread-2")
            assert "Answer 2" in browser.preview()
            pipe.send_text("\x14")
            await _until(lambda: shell.history_browser is not None and shell.history_browser.page is not None)
            assert backend.app.get_thread_transcript.call_args.kwargs["thread_id"] == "thread-2"
            assert backend.thread_id == "thread-current"
            pipe.send_text("q")
            await _until(lambda: shell.history_browser is None)
            assert browser.selected.thread_id == "thread-2"
            assert shell.app.layout.has_focus(browser.search)
            # Failure is visible inside the browser, never silently switches or clears attachments.
            pipe.send_text("\r")
            await _until(lambda: "already running" in browser.message and not browser.saving)
            assert shell.resume_browser is browser and backend.thread_id == "thread-current"
            assert shell.images and shell.composer.buffer.document == draft
            # Ordinary letters and bracketed paste belong to search, not generic menu shortcuts.
            pipe.send_text("rq\x1b[200~ exact %\x1b[201~")
            await _until(lambda: not browser.loading and browser.search.text == "rq exact %")
            assert backend.resume_sessions.call_args.kwargs["query"] == "rq exact %"
            pipe.send_text("\x1bOQ")  # F2
            await _until(lambda: browser.renaming is not None)
            pipe.send_text("My session\r")
            await _until(lambda: backend.app.update_thread_metadata.await_count == 1 and not browser.loading)
            mutation = backend.app.update_thread_metadata.call_args.kwargs["mutation"]
            assert mutation.expected_version == 1 and mutation.patch.title == "My session"
            pipe.send_text("\x1bOQ")
            await _until(lambda: browser.renaming is not None)
            pipe.send_text("\r")
            await _until(lambda: backend.app.update_thread_metadata.await_count == 2 and not browser.loading)
            assert backend.app.update_thread_metadata.call_args.kwargs["mutation"].patch.title is None
            pipe.send_text("\x1b")
            await _until(lambda: shell.resume_browser is None)
            assert shell.composer.buffer.document == draft
            assert shell.pastes.expand(draft.text) == "draft " + "large paste " * 200
            assert shell.images[0].name == "picture.png"
            assert "Original live conversation" in _source(shell)
            assert "Retained answer" not in _source(shell)
        finally:
            shell.close_history()
            shell.close_resume()
            shell.app.exit()
            await terminal
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_pages_scope_inspection_and_successful_switch(monkeypatch) -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = backend = _backend()
        monkeypatch.setattr(shell, "_load_notes", AsyncMock())
        shell.images = (AttachmentUpload("picture.png", b"fixture", "image/png"),)
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await _until(lambda: shell.app.is_running)
            shell.open_resume()
            await _until(lambda: shell.resume_browser is not None and shell.resume_browser.selected is not None)
            browser = shell.resume_browser
            backend.resume_sessions.return_value = ThreadPage(threads=(_thread(3),), total=3)
            pipe.send_text("\x1b[6~")
            await _until(lambda: not browser.loading and browser.page_index == 1)
            assert backend.resume_sessions.call_args.kwargs["cursor"] == "page2"
            pipe.send_text("\x1b[5~")
            await _until(lambda: not browser.loading and browser.page_index == 0)
            assert backend.resume_sessions.call_args.kwargs["cursor"] is None
            backend.resume_sessions.return_value = ThreadPage(threads=(_thread(4, project="project-other"),), total=1)
            pipe.send_text("\x01")
            await _until(lambda: not browser.loading and browser.all_directories)
            assert backend.resume_sessions.call_args.kwargs["all_directories"]
            assert "/elsewhere" in browser.guidance(browser.selected)
            pipe.send_text("\r")
            await _until(lambda: "--resume thread-4" in browser.message)
            backend.resume.assert_not_called()
            pipe.send_text("\x14")
            await _until(lambda: shell.history_browser is not None and shell.history_browser.page is not None)
            pipe.send_text("q")
            await _until(lambda: shell.history_browser is None)
            backend.resume_sessions.return_value = ThreadPage(threads=(_thread(3),), total=1)
            pipe.send_text("\x01")
            await _until(lambda: not browser.loading and not browser.all_directories)

            async def resume(thread_id):
                backend.thread_id = thread_id
                backend.resumed_transcript = backend.app.get_thread_transcript.return_value
                return f"Resumed {thread_id}"

            backend.resume.side_effect = resume
            pipe.send_text("\r")
            await _until(lambda: shell.resume_browser is None and not shell.busy)
            assert backend.thread_id == "thread-3"
            assert not shell.images
            assert "Retained answer" in _source(shell)
        finally:
            shell.close_history()
            shell.close_resume()
            shell.app.exit()
            await terminal
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_stale_search_and_closed_browser_discard_late_responses() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = backend = _backend()
        shell.open_resume()
        await _until(lambda: shell.resume_browser.selected is not None)
        browser = shell.resume_browser
        started, release = asyncio.Event(), asyncio.Event()

        async def load(**kwargs):
            if kwargs["query"] == "slow":
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return ThreadPage(threads=(_thread(99),), total=1)
            return ThreadPage(threads=(_thread(3),), total=1)

        backend.resume_sessions.side_effect = load
        browser.search.text = "slow"
        await started.wait()
        browser.search.text = "fast"
        await _until(lambda: browser.selected is not None and browser.selected.thread_id == "thread-3")
        release.set()
        await asyncio.sleep(0.03)
        assert browser.selected.thread_id == "thread-3"
        browser.search.text = "closed"
        shell.close_resume()
        await asyncio.sleep(0.2)
        assert browser.closed and browser.page.threads[0].thread_id == "thread-3"
        shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_browser_resize_keeps_search_and_selection_visible(monkeypatch) -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = _backend()
        shell.open_resume()
        await _until(lambda: shell.resume_browser.selected is not None)
        browser = shell.resume_browser
        with set_app(shell.app):
            for width, height in ((120, 40), (80, 24), (40, 12), (24, 6), (80, 24)):
                monkeypatch.setattr(
                    shell.app.output, "get_size", lambda height=height, width=width: Size(rows=height, columns=width)
                )
                shell.app.renderer.render(shell.app, shell.app.layout)
                assert shell.app.layout.has_focus(browser.search)
                assert browser.search.window.render_info.window_height == 1
                assert all(get_cwidth(line) <= width for _, text in browser.rows() for line in text.splitlines())
                assert all(get_cwidth(line) <= width for line in shell._hints().splitlines())
        shell.close_resume()
        shell.renderer.transcript.close()
