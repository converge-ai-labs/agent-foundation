"""Host feedback has one identifiable style without changing retained content."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.interactive.theme import activity_colors, resolve_theme
from a13n_harness_ui.interactive.transcript import Transcript
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth


@pytest.mark.parametrize("preference", ["auto", "dark", "light"])
@pytest.mark.parametrize("width", [24, 80])
def test_system_notices_have_a_common_marker_and_theme_aware_style(preference: str, width: int) -> None:
    transcript = Transcript()
    try:
        transcript.theme = resolve_theme(preference, environ={})
        message = "Guidance sent. [literal]"
        block_id = transcript.append(message, kind="notice")
        transcript.render(width)
        rows = list(transcript.rows)
        text = "\n".join("".join(part for _, part in row) for row in rows)
        assert "System" in text
        assert "Guidance" in text and "[literal]" in text
        assert all(get_cwidth("".join(part for _, part in row)) <= width for row in rows)
        assert transcript.blocks[block_id].source == message
        assert any("bold" in style and "System" in part for row in rows for style, part in row)
        muted = activity_colors(transcript.theme)["muted"]
        muted = muted if muted.startswith("#") else "ansi" + muted.replace("_", "")
        assert any(f"fg:{muted}" in style and "Guidance" in part for row in rows for style, part in row)
        if width == 80:
            assert len([row for row in rows if any(part.strip() for _, part in row)]) == 1
    finally:
        transcript.close()


def test_shell_feedback_defaults_to_system_notices_not_assistant_text() -> None:
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        try:
            for message in (
                "Guidance sent.",
                "Command accepted: /ps",
                "No matching command; treating the original input as plain text.",
                "Cancelled.",
            ):
                shell.emit(message)
            blocks = list(shell.renderer.transcript.blocks.values())
            assert all(block.kind == "notice" for block in blocks)
            shell.renderer.transcript.render(100)
            text = "\n".join("".join(part for _, part in row) for row in shell.renderer.transcript.rows)
            assert text.count("System") == 4
            assert "System" not in "".join(block.source for block in blocks)
        finally:
            shell.renderer.transcript.close()


@pytest.mark.anyio
@pytest.mark.parametrize("input_text", ["\x03", "/cancel\r"])
async def test_run_cancellation_uses_status_until_one_final_notice(input_text: str) -> None:
    requested, cleanup = asyncio.Event(), asyncio.Event()

    async def cancel() -> None:
        requested.set()

    async def operation() -> str:
        await requested.wait()
        await cleanup.wait()
        return "Run cancelled."

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace(
            thread_id=None,
            receipt_id="receipt-active",
            cancel=cancel,
            interaction=AsyncMock(return_value=None),
            skill_catalog=AsyncMock(return_value=None),
        )
        shell.ready = True
        shell.launch(operation(), kind="run")
        terminal = asyncio.create_task(shell.app.run_async())
        expected = ["Command accepted: /cancel"] if input_text.startswith("/") else []

        def notices() -> list[str]:
            return [
                block.source.strip() for block in shell.renderer.transcript.blocks.values() if block.kind == "notice"
            ]

        try:
            async with asyncio.timeout(3):
                while not shell.app.is_running:
                    await asyncio.sleep(0.01)
            pipe.send_text(input_text)
            await asyncio.wait_for(requested.wait(), 3)
            assert shell.busy
            assert shell.status.state == "cancelling"
            assert notices() == expected

            # Another interrupt during cleanup must not add transcript noise.
            requested.clear()
            pipe.send_text("\x03")
            await asyncio.wait_for(requested.wait(), 3)
            assert shell.busy
            assert notices() == expected

            cleanup.set()
            await asyncio.wait_for(shell.job, 3)
            assert shell.status.state == "ready"
            assert notices() == [*expected, "Run cancelled."]
        finally:
            requested.set()
            cleanup.set()
            await asyncio.gather(shell.job, return_exceptions=True)
            if shell.app.is_running:
                shell.app.exit()
            await terminal
            shell.renderer.transcript.close()


def test_markdown_notices_keep_formatting_and_ordinary_messages_are_not_relabeled() -> None:
    transcript = Transcript()
    try:
        transcript.append("## Commands\n\n**Important** command help", markdown=True, kind="notice")
        transcript.append("Assistant answer", markdown=True)
        transcript.append("User message", kind="user")
        transcript.render(80)
        text = "\n".join("".join(part for _, part in row) for row in transcript.rows)
        assert text.count("System") == 1
        assert "Commands" in text and "Important" in text
        assert "**Important**" not in text
        assert "Assistant answer" in text and "User message" in text
    finally:
        transcript.close()
