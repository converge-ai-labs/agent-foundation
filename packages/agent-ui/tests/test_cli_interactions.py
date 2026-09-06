from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from a13n_ui.cli import CliRequest
from a13n_ui.interactive.decisions import DecisionInteraction
from a13n_ui.interactive.selection import Choice, Selection, resolve_choice
from a13n_ui.interactive.setup import SetupWizard
from a13n_ui.interactive.shell import CliShell
from a13n_ui.surfaces import (
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
    wizard.accept("2")
    assert wizard.question is not None and wizard.question.key == "route"
    wizard.accept("")
    wizard.accept("key:work")
    wizard.accept("1")
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

    @asynccontextmanager
    async def factory(*args):
        yield Backend()

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest(), directory=tmp_path, runtime_loader=lambda: factory)
        task = asyncio.create_task(shell.run())
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
async def test_cancel_discards_partial_command_mode_approval_drafts(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from a13n_ui.interactive.backend import SessionBackend
    from a13n_ui.interactive.rendering import Status

    detail = SimpleNamespace(
        continuation_id="a" * 64,
        deferred_requests=(
            SimpleNamespace(request_id="one", kind="approval"),
            SimpleNamespace(request_id="two", kind="approval"),
        ),
    )

    class App:
        async def get_thread(self, thread_id):
            return detail

    backend = SessionBackend(App(), CliRequest(), tmp_path, Status())
    backend.thread_id = "thread-one"
    assert await backend.decide("approve", "one") is None
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = backend
        await shell.cancel()
    assert await backend.decide("deny", "two") is None
    response = await backend.decide("deny", "one")
    assert response is not None
    assert all(isinstance(item, ApprovalDecision) and not item.approved for item in response.responses)
