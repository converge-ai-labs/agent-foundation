"""Known slash commands are explicit; unmatched slash prose stays authored input."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.commands import CommandRegistry
from a13n_harness_ui.interactive.shell import CliShell
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


async def _until(predicate) -> None:
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


def _notices(shell: CliShell) -> str:
    return "\n".join(block.source for block in shell.renderer.transcript.blocks.values())


@pytest.mark.parametrize("text", ["/ps-more details", "/ps\u7684\u65f6\u5019 I would like changes", "/unknown"])
@pytest.mark.anyio
async def test_enter_sends_unmatched_slash_input_unchanged_with_notice(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    received = []
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        shell.backend = SimpleNamespace(thread_id=None, receipt_id=None)
        monkeypatch.setattr(shell, "send_prompt", received.append)
        command = AsyncMock()
        monkeypatch.setattr(shell, "command", command)
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await _until(lambda: shell.app.is_running)
            pipe.send_text(text)
            await _until(lambda: shell.composer.text == text)
            pipe.send_text("\r")
            await _until(lambda: bool(received))
            assert received == [text]
            assert not command.called
            assert "No matching command; treating the original input as plain text." in _notices(shell)
            assert "Command accepted" not in _notices(shell)
            assert shell.composer.text == ""
        finally:
            if shell.app.is_running:
                shell.app.exit()
            await terminal
            shell.renderer.transcript.close()


@pytest.mark.parametrize(
    "text, name", [("/ps", "ps"), ("/?", "help"), ("/mode detailed", "mode"), ("/fast", "fast"), ("/fast off", "fast")]
)
@pytest.mark.anyio
async def test_recognized_commands_get_one_acceptance_notice(
    text: str, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        command = AsyncMock()
        monkeypatch.setattr(shell, "command", command)
        await shell.handle(text)
        assert command.await_args.args[0].command.name == name
        assert _notices(shell).count(f"Command accepted: /{name}") == 1
        assert "plain text" not in _notices(shell)
        shell.renderer.transcript.close()


@pytest.mark.parametrize("text", ["/ps extra", "/mode invalid", '/attach "unclosed', "/fast invalid", "/fast on extra"])
@pytest.mark.anyio
async def test_known_command_errors_never_fall_back_to_model(text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        command = AsyncMock()
        monkeypatch.setattr(shell, "command", command)
        await shell.handle(text)
        assert not command.called
        assert shell.composer.text == text
        assert "plain text" not in _notices(shell)
        assert "Command accepted" not in _notices(shell)
        shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_unmatched_slash_steers_exact_active_run_with_notice() -> None:
    text = "/ps-like output needs clearer colors"
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        steer = AsyncMock(return_value="Guidance sent")
        shell.backend = SimpleNamespace(thread_id="thread-one", receipt_id="receipt-one", steer=steer)
        shell.job_kind = "run"
        shell.status.state = "working"
        shell.job = asyncio.create_task(asyncio.Event().wait())
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await _until(lambda: shell.app.is_running)
            shell.composer.text = text
            pipe.send_text("\r")
            await _until(lambda: steer.called)
            await shell._input_task
            steer.assert_awaited_once_with(text, receipt_id="receipt-one", skill_references=())
            assert "plain text" in _notices(shell)
            assert "Guidance sent" in _notices(shell)
        finally:
            if shell.app.is_running:
                shell.app.exit()
            await terminal
            shell.job.cancel()
            await asyncio.gather(shell.job, return_exceptions=True)
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_unknown_slash_does_not_bypass_readiness_or_clear_draft() -> None:
    text = "/unknown please keep this draft"
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await _until(lambda: shell.app.is_running)
            shell.composer.text = text
            pipe.send_text("\r")
            await _until(lambda: bool(shell.renderer.transcript.blocks))
            assert shell.composer.text == text
            assert shell._input_task is None
            assert "draft is preserved" in _notices(shell)
            assert "plain text" not in _notices(shell)
        finally:
            if shell.app.is_running:
                shell.app.exit()
            await terminal
            shell.renderer.transcript.close()


def test_lookup_uses_complete_command_or_alias_not_prose_prefix() -> None:
    registry = CommandRegistry()
    assert registry.lookup("/ps").name == "ps"
    assert registry.lookup("/ps\tanything").name == "ps"
    assert registry.lookup("/?").name == "help"
    assert registry.lookup("/ps-like text") is None
    assert registry.lookup("/ps\u7684\u65f6\u5019") is None


@pytest.mark.anyio
async def test_fast_during_active_work_preserves_draft_without_changing_tier() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        fast = AsyncMock()
        shell.backend = SimpleNamespace(thread_id="thread-one", fast=fast)
        shell.job_kind = "run"
        shell.job = asyncio.create_task(asyncio.Event().wait())
        try:
            await shell.handle("/fast off")
            fast.assert_not_called()
            assert shell.composer.text == "/fast off"
            assert "plain text" not in _notices(shell)
        finally:
            shell.job.cancel()
            await asyncio.gather(shell.job, return_exceptions=True)
            shell.renderer.transcript.close()
