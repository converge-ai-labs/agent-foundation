from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.attachments import image_bytes
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.thread_files import ComposerInput
from PIL import Image
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


async def _until(predicate) -> None:
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.anyio
@pytest.mark.parametrize("reject", [False, True])
@pytest.mark.parametrize("new_draft", [False, True])
@pytest.mark.parametrize("guidance,with_image", [("change direction", False), ("change direction", True), ("", True)])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("local_command", [False, True])
async def test_enter_targets_exact_run_and_preserves_rejected_guidance(
    reject: bool, new_draft: bool, guidance: str, with_image: bool, explicit: bool, local_command: bool
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    received = []

    class Backend:
        thread_id = "thread-one"
        receipt_id = "receipt-original"

        async def steer(self, message, *, receipt_id, skill_references=()):
            received.append((receipt_id, message))
            entered.set()
            await release.wait()
            if reject:
                raise ValueError("Target already completed; guidance was not sent")
            return "Guidance sent"

        async def execute(self, *args, **kwargs):
            pytest.fail("An active-run Enter must never become a new turn")

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        backend = Backend()
        shell.backend = backend
        shell.ready = True
        shell.job_kind = "run"
        shell.status.state = "working"
        shell.job = asyncio.create_task(asyncio.Event().wait())
        if with_image:
            data = BytesIO()
            Image.new("RGB", (1, 1)).save(data, format="PNG")
            shell.insert_attachments((image_bytes("draft.png", data.getvalue()),))
        images = shell.images
        if explicit:
            shell.composer.buffer.cursor_position = 0
            shell.composer.buffer.insert_text("/steer ")
            shell.composer.buffer.cursor_position = len(shell.composer.text)
        draft_text = ("/steer " if explicit else "") + guidance
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await _until(lambda: shell.app.is_running)
            pipe.send_text(guidance + "\r")
            await asyncio.wait_for(entered.wait(), 3)
            backend.receipt_id = "receipt-replacement"
            if local_command:
                pipe.send_text("/help\r")
                await _until(
                    lambda: any("## Commands" in block.source for block in shell.renderer.transcript.blocks.values())
                )
                assert shell._sending_draft is not None
                assert shell.inline.compile(shell._sending_draft.text).attachments == images
            if new_draft:
                pipe.send_text("next draft")
                await _until(lambda: shell.composer.text == "next draft")
            release.set()
            await _until(lambda: shell._input_task.done())
            assert len(received) == 1
            receipt, prompt = received[0]
            assert receipt == "receipt-original"
            assert isinstance(prompt, ComposerInput)
            assert prompt.text == guidance
            assert prompt.attachments == images
            if reject and new_draft:
                assert shell.composer.text == "next draft"
                assert shell._recoverable is not None
                recovered = shell.inline.compile(shell._recoverable.text)
                assert recovered.text == draft_text
                assert recovered.attachments == images
            elif reject:
                restored = shell.inline.compile(shell.composer.text)
                assert restored.text == draft_text
                assert restored.attachments == images
            else:
                assert shell.composer.text == ("next draft" if new_draft else "")
                assert any("Guidance sent" in item.source for item in shell.renderer.transcript.blocks.values())
            assert shell.job_kind == "run" and not shell.job.done()
        finally:
            release.set()
            if shell.app.is_running:
                shell.app.exit()
            await terminal
            shell.job.cancel()
            await asyncio.gather(shell.job, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("blocked", ["preparing", "cancelling", "login"])
async def test_enter_preserves_draft_when_operation_cannot_accept_it(blocked: str) -> None:
    class Backend:
        thread_id = "thread-one"
        receipt_id = None if blocked == "preparing" else "receipt-active"

        async def steer(self, *args, **kwargs):
            pytest.fail("Blocked input must not be submitted")

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = Backend()
        shell.ready = True
        shell.job_kind = "login" if blocked == "login" else "run"
        shell.status.state = "cancelling" if blocked == "cancelling" else "working"
        shell.job = asyncio.create_task(asyncio.Event().wait())
        data = BytesIO()
        Image.new("RGB", (1, 1)).save(data, format="PNG")
        shell.insert_attachments((image_bytes("draft.png", data.getvalue()),))
        images = shell.images
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await _until(lambda: shell.app.is_running)
            pipe.send_text("keep this\r")
            await _until(lambda: bool(shell.renderer.transcript.blocks))
            assert shell.inline.compile(shell.composer.text).text == "keep this"
            assert shell.images == images
            assert shell._input_task is None
        finally:
            if shell.app.is_running:
                shell.app.exit()
            await terminal
            shell.job.cancel()
            await asyncio.gather(shell.job, return_exceptions=True)


def test_setup_is_not_a_chat_command() -> None:
    from a13n_harness_ui.interactive.commands import CommandRegistry

    registry = CommandRegistry()
    with pytest.raises(ValueError, match="Unknown command"):
        registry.parse("/setup")
    assert registry.completions("/setup") == ()


def test_native_delivery_feedback_is_distinct_from_acceptance() -> None:
    from a13n_harness_ui.interactive.rendering import Status, StreamRenderer

    renderer = StreamRenderer(Status())
    renderer.ingest(
        "CUSTOM",
        {
            "name": "a13n.pydantic_ai.enqueued_messages",
            "value": {"event": {"event_kind": "enqueued_messages", "enqueue_id": "one"}},
        },
    )
    assert renderer.drain() == ""
