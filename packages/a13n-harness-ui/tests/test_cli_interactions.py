from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.decisions import DecisionInteraction
from a13n_harness_ui.interactive.selection import Choice, Selection, resolve_choice
from a13n_harness_ui.interactive.setup import SetupWizard
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.surfaces import (
    ApprovalDecision,
    ApprovalRequestView,
    DecisionBatchView,
    ExternalToolResult,
    QuestionOptionView,
    QuestionView,
    StructuredQuestionRequestView,
    ThreadDeferredResponse,
)
from prompt_toolkit.application import create_app_session
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


def _batch() -> DecisionBatchView:
    options = (
        QuestionOptionView(label="One", description="First option"),
        QuestionOptionView(label="Two", description="Second option"),
    )
    return DecisionBatchView(
        continuation_id="a" * 64,
        requests=(
            StructuredQuestionRequestView(
                request_id="question",
                tool_name="ask_user_question",
                questions=(
                    QuestionView(header="Single", question="Which one?", options=options),
                    QuestionView(header="Multiple", question="Which several?", options=options, multi_select=True),
                ),
            ),
            ApprovalRequestView(request_id="shell", tool_name="shell_exec", arguments={"command": "echo example"}),
        ),
    )


@pytest.mark.parametrize(
    "path",
    [r"C:\Users\me\picture.png", r"\\server\share\picture.png", r'"C:\My Photos\picture.png"', "'/tmp/my photo.png'"],
)
def test_attachment_paths_preserve_native_backslashes(path: str) -> None:
    from a13n_harness_ui.interactive.commands import CommandRegistry

    assert CommandRegistry().parse(f"/attach {path}").arguments == (path.strip("\"'"),)


def test_literal_command_tails_preserve_json_and_steering() -> None:
    from a13n_harness_ui.interactive.commands import CommandRegistry

    registry = CommandRegistry()
    value = r'{"path": "C:\\work\\image.png", "answer": "two words"}'
    with pytest.raises(ValueError, match="Unknown command"):
        registry.parse(f"/result request {value}")
    message = "Use 'this' path C:\\work\nand keep the spaces  here"
    assert registry.parse(f"/steer {message}", busy=True).arguments == (message,)
    with pytest.raises(ValueError, match="Usage"):
        registry.parse("/steer  ")


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "30"])
def test_question_timeout_configuration_rejects_invalid_values(timeout: object) -> None:
    from a13n_harness_ui.configuration.models import ToolsConfiguration

    with pytest.raises(ValueError):
        ToolsConfiguration.model_validate({"user_input_timeout_seconds": timeout})


def test_question_timeout_is_per_question_and_never_approves_other_requests() -> None:
    interaction = DecisionInteraction(_batch(), timeout_seconds=30)
    interaction.question_started -= 31
    assert interaction.expired
    assert interaction.accept("One") is None
    assert not interaction.expired
    interaction.question_started -= 31
    assert interaction.expire_question() is None
    assert interaction.responses == [
        ExternalToolResult(
            request_id="question",
            denied=True,
            denial_message=interaction.responses[0].denial_message,
        )
    ]
    assert not interaction.answers
    assert isinstance(interaction.request, ApprovalRequestView)
    assert not interaction.expired
    assert interaction.selection().cursor == -1
    response = interaction.accept("deny")
    assert isinstance(response, ThreadDeferredResponse)
    assert response.expected_continuation_id == _batch().continuation_id
    assert isinstance(response.responses[1], ApprovalDecision) and not response.responses[1].approved


@pytest.mark.anyio
async def test_terminal_question_timeout_rejects_call_then_keeps_shell_approval_pending() -> None:
    from types import SimpleNamespace

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace()
        shell.ready = True
        shell.composer.text = "preserved draft"
        shell._save_draft()
        shell.interaction = DecisionInteraction(_batch(), timeout_seconds=0.01)
        shell.selection = shell.interaction.selection()
        flusher = asyncio.create_task(shell._flusher())
        try:
            async with asyncio.timeout(2):
                while shell.interaction.index == 0:
                    await asyncio.sleep(0.01)
            assert shell.interaction.responses[0].denied
            assert isinstance(shell.interaction.request, ApprovalRequestView)
            assert shell.job is None
            assert shell._saved_draft.text == "preserved draft"
            assert "Timed out" in "".join(block.source for block in shell.renderer.transcript.blocks.values())
            await asyncio.sleep(0.1)
            assert shell.interaction.index == 1
        finally:
            shell.closing = True
            await flusher
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_closing_during_question_wait_does_not_submit_a_timeout() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        shell.interaction = DecisionInteraction(_batch(), timeout_seconds=0.01)
        flusher = asyncio.create_task(shell._flusher())
        await asyncio.sleep(0)
        shell.closing = True
        await flusher
        assert not shell.interaction.responses
        assert shell.interaction.index == 0
        shell.renderer.transcript.close()


def test_question_transcript_retains_option_descriptions() -> None:
    prompt = DecisionInteraction(_batch()).prompt()
    assert "1. One\n   First option" in prompt
    assert "2. Two\n   Second option" in prompt


def test_selection_does_not_implicitly_choose_and_validates_numbers() -> None:
    selection = Selection((Choice("one", "One"), Choice("two", "Two")))
    with pytest.raises(ValueError, match="Choose"):
        selection.answer()
    selection.move(1)
    assert selection.answer() == "1"
    selection.move(-1)
    assert selection.answer() == "2"
    for invalid in ("0", "3", "1,2"):
        with pytest.raises(ValueError):
            resolve_choice(invalid, ("One", "Two"))
    with pytest.raises(ValueError, match="only once"):
        resolve_choice("1,1", ("One", "Two"), multiple=True)
    assert resolve_choice("Custom answer", ("One", "Two")) == "Custom answer"
    selection.multiple = True
    selection.toggle()
    selection.move(-1)
    selection.toggle()
    assert selection.answer() == "1,2"


def test_unselected_approval_arrows_start_at_the_correct_edge() -> None:
    choices = (Choice("review", "Inspect"), Choice("yes", "Approve once"), Choice("no", "Deny"))
    upward = Selection(choices)
    upward.move(0)
    assert upward.cursor == -1
    upward.move(-1)
    assert upward.answer() == "3"
    upward.move(1)
    assert upward.answer() == "1"
    downward = Selection(choices)
    downward.move(1)
    assert downward.answer() == "1"


def test_question_and_approval_batch_is_typed_and_complete() -> None:
    interaction = DecisionInteraction(_batch())
    assert interaction.accept("2") is None
    assert interaction.accept("1,2") is None
    assert interaction.accept("review") == "review"
    for invalid in ("", "please do it", "0", "4"):
        with pytest.raises(ValueError):
            interaction.accept(invalid)
    result = interaction.accept("no Not this command")
    assert isinstance(result, ThreadDeferredResponse)
    assert result.expected_continuation_id == "a" * 64
    question, approval = result.responses
    assert isinstance(question, ExternalToolResult)
    assert question.result == {"answers": {"Which one?": "Two", "Which several?": ["One", "Two"]}}
    assert approval.model_dump()["approved"] is False
    assert approval.model_dump()["denial_message"] == "Not this command"


def test_setup_access_selection_and_back_preserve_no_secret_defaults() -> None:
    wizard = SetupWizard()
    wizard.accept("3")
    assert wizard.question is not None and wizard.question.key == "route"
    wizard.accept("")
    wizard.accept("key:work")
    wizard.accept("1")
    wizard.customize()
    wizard.accept("Keep replies concise")
    assert wizard.question is None
    selection = wizard.selection("/workspace")
    assert selection["instructions"] == "Keep replies concise"
    assert selection["providers"] == []
    assert selection["api_key_model"] == {
        "route": "openai-responses:gpt-5.6-sol",
        "authentication": {"kind": "api_key", "credential_ref": "work"},
    }
    assert wizard.back()
    assert wizard.question is not None and wizard.question.key == "instructions"
    assert wizard.preview_generation is None


@pytest.mark.anyio
async def test_inline_decision_keys_preserve_preexisting_draft(tmp_path: Path) -> None:
    """Real prompt-toolkit input dispatch, also runnable on Windows without a PTY."""
    ready = asyncio.Event()
    submitted = asyncio.Event()
    allow_decision = asyncio.Event()
    responses = []

    class Backend:
        thread_id = None

        async def skill_catalog(self):
            return None

        async def initialize(self):
            ready.set()
            return True

        async def interaction(self):
            return DecisionInteraction(_batch()) if allow_decision.is_set() else None

        async def execute(self, renderer, *, response=None, prompt=None, flush=None, admitted=None):
            if response is not None:
                responses.append(response)
                allow_decision.clear()
            submitted.set()
            return ""

        async def cancel(self):
            return None

    backend = Backend()
    await backend.initialize()

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest(), directory=tmp_path)
        task = asyncio.create_task(shell.run(backend))
        try:
            await ready.wait()
            async with asyncio.timeout(3):
                while not shell.app.is_running:
                    await asyncio.sleep(0.01)
            shell.composer.buffer.document = Document("keep this draft", 5)
            allow_decision.set()
            await shell._activate_decisions()
            assert shell.composer.buffer.text == ""
            pipe.send_text("\x1b[B\r")  # choose One
            async with asyncio.timeout(3):
                while shell.interaction.question_index != 1:
                    await asyncio.sleep(0.01)
            pipe.send_text("\x1b[B \x1b[B \r")  # select both
            async with asyncio.timeout(3):
                while shell.interaction.index != 1:
                    await asyncio.sleep(0.01)
            pipe.send_text("yes\r")
            await asyncio.wait_for(submitted.wait(), 3)
            assert len(responses) == 1
            async with asyncio.timeout(3):
                while shell.composer.buffer.text != "keep this draft":
                    await asyncio.sleep(0.01)
            assert shell.composer.buffer.cursor_position == 5
            assert shell.interaction is None
            async with asyncio.timeout(3):
                while shell.busy:
                    await asyncio.sleep(0.01)
            pipe.send_text("\x03/quit\r")
            await asyncio.wait_for(task, 3)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_cancel_discards_partial_native_decision_drafts() -> None:
    from a13n_harness_ui.interactive.commands import CommandRegistry

    batch = DecisionBatchView(
        continuation_id="a" * 64,
        requests=(
            ApprovalRequestView(request_id="one", tool_name="shell_exec"),
            ApprovalRequestView(request_id="two", tool_name="shell_exec"),
        ),
    )
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.interaction = DecisionInteraction(batch)
        assert shell.interaction.accept("approve") is None
        await shell.cancel()
        assert shell.interaction is None
        fresh = DecisionInteraction(batch)
        assert fresh.accept("deny") is None
        response = fresh.accept("deny")
        assert isinstance(response, ThreadDeferredResponse)
        assert all(isinstance(item, ApprovalDecision) and not item.approved for item in response.responses)
    for name in ("approve", "deny", "result", "login"):
        with pytest.raises(ValueError, match="Unknown command"):
            CommandRegistry().parse(f"/{name} one")


@pytest.mark.anyio
async def test_same_input_batch_cancellation_prevents_admission(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock, Mock

    from a13n_harness_ui.app import HarnessUiApp
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.interactive.rendering import Status

    app = Mock(spec=HarnessUiApp)
    backend = SessionBackend(app, CliRequest(), tmp_path, Status())
    backend.initialize = AsyncMock(return_value=True)
    backend.interaction = AsyncMock(return_value=None)
    backend.refresh = AsyncMock()

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest(), directory=tmp_path)
        task = asyncio.create_task(shell.run(backend))
        try:
            async with asyncio.timeout(3):
                while not shell.ready or not shell.app.is_running:
                    await asyncio.sleep(0.01)
            pipe.send_text("hello\r\x03")
            async with asyncio.timeout(3):
                while not backend.cancel_requested or shell.busy or shell.composer.text != "hello":
                    await asyncio.sleep(0.01)
            app.submit_thread.assert_not_called()
            backend.refresh.assert_not_called()
            assert shell.composer.text == "hello"
            pipe.send_text("\x03/quit\r")
            await asyncio.wait_for(task, 3)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("newer_draft", [False, True])
async def test_rejected_resume_preserves_images_and_command(tmp_path: Path, newer_draft: bool) -> None:
    from unittest.mock import AsyncMock, Mock

    from a13n_harness_ui.interactive.attachments import DraftImage
    from a13n_harness_ui.interactive.backend import SessionBackend

    release = asyncio.Event()

    async def reject(_argument):
        await release.wait()
        raise ValueError("Session does not exist")

    backend = Mock(spec=SessionBackend)
    backend.resume = AsyncMock(side_effect=reject)
    backend.interaction = AsyncMock(return_value=None)
    image = DraftImage("image.png", b"test fixture", "image/png")
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest(), directory=tmp_path)
        shell.backend = backend
        shell.images = (image,)
        await shell.command(shell.registry.parse("/resume missing"))
        if newer_draft:
            shell.composer.buffer.document = Document("new draft")
        release.set()
        await shell.job
        assert shell.images == (image,)
        assert shell.composer.text == ("new draft" if newer_draft else "/resume missing")
        if newer_draft:
            await shell.command(shell.registry.parse("/recover"))
            assert shell.composer.text == "/resume missing"
            assert shell.images == (image,)


@pytest.mark.anyio
async def test_steering_captures_receipt_and_does_not_retarget(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock, Mock

    from a13n_harness_ui.app import HarnessUiApp
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.interactive.rendering import Status
    from a13n_harness_ui.surfaces import RootControlResult

    app = Mock(spec=HarnessUiApp)
    backend = SessionBackend(app, CliRequest(), tmp_path, Status())
    backend.receipt_id = "first"

    async def steer(*, receipt_id, message, skill_references):
        backend.receipt_id = "replacement"
        return RootControlResult(receipt_id=receipt_id, accepted=False)

    app.steer_root_operation = AsyncMock(side_effect=steer)
    with pytest.raises(ValueError, match="not accepted"):
        await backend.steer("keep changes small")
    app.steer_root_operation.assert_awaited_once_with(
        receipt_id="first", message="keep changes small", skill_references=()
    )
    app.steer_root_operation = AsyncMock(
        return_value=RootControlResult(receipt_id="replacement", accepted=True, enqueue_id="input-one")
    )
    assert await backend.steer("new guidance") == "Guidance sent. It will appear as input when applied."
    backend.receipt_id = None
    with pytest.raises(ValueError, match="No running receipt"):
        await backend.steer("not sent")


@pytest.mark.anyio
async def test_rejected_steering_can_be_recovered_without_overwriting_newer_draft(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock, Mock

    from a13n_harness_ui.interactive.backend import SessionBackend

    entered, release = asyncio.Event(), asyncio.Event()

    async def reject(message):
        entered.set()
        await release.wait()
        raise ValueError("Receipt is no longer running")

    backend = Mock(spec=SessionBackend)
    backend.steer = AsyncMock(side_effect=reject)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest(), directory=tmp_path)
        shell.backend = backend
        task = asyncio.create_task(shell.handle("/steer important guidance"))
        await asyncio.wait_for(entered.wait(), 3)
        shell.composer.buffer.document = Document("new draft")
        release.set()
        await task
        assert shell.composer.text == "new draft"
        await shell.command(shell.registry.parse("/recover"))
        assert shell.composer.text == "/steer important guidance"
        backend.steer.assert_awaited_once_with("important guidance")
