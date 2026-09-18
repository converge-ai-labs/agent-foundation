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
        ToolsConfiguration.model_validate({"interaction_timeout_seconds": timeout})


def test_question_timeout_is_per_question_and_never_approves_other_requests() -> None:
    interaction = DecisionInteraction(_batch(), timeout_seconds=30)
    assert interaction.accept("One") is None
    assert not interaction.expired
    interaction.request_started -= 31
    assert interaction.expire() is None
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
async def test_terminal_timeout_denies_questions_and_tool_approvals_uniformly(monkeypatch) -> None:
    from types import SimpleNamespace

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace()
        completed = []
        original_finish = shell._finish_decision

        def finish(response):
            if response is None:
                original_finish(response)
            else:
                completed.append(response)
                shell.interaction = None

        monkeypatch.setattr(shell, "_finish_decision", finish)
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
            async with asyncio.timeout(2):
                while not completed:
                    await asyncio.sleep(0.01)
            assert len(completed) == 1
            assert completed[0].responses[0].denied
            assert not completed[0].responses[1].approved
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
    for invalid in ("", "please do it", "0", "5"):
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
    wizard = SetupWizard(advanced=True)
    wizard.accept("3")
    assert wizard.question is not None and wizard.question.key == "api_provider"
    wizard.accept("")
    wizard.accept("")
    wizard.accept("off")
    wizard.accept("new")
    wizard.accept("key:key-work")
    wizard.accept("gpt-5.6-sol")
    wizard.accept("high")
    wizard.accept("all")
    wizard.accept("")
    wizard.accept("")  # Tool recommendations.
    wizard.accept("yes")  # Root shell-review shortcut, including API connections.
    wizard.accept("Keep replies concise")
    wizard.accept("1")
    assert wizard.question is None
    selection = wizard.selection("/workspace")
    assert selection["instructions"] == "Keep replies concise"
    assert selection["model"] == {
        "route": "openai-responses:gpt-5.6-sol",
        "authentication": {"kind": "api_key", "credential_ref": "key-work", "env": None},
        "model_configuration": {"base_url": "https://api.openai.com/v1"},
        "model_characteristics": {
            "capabilities": ["image_understanding"],
            "context_window_tokens": 350000,
            "proactive_context_management_threshold": 0.65,
            "compact_threshold": 0.90,
        },
        "settings": {
            "thinking": "high",
            "openai_reasoning_summary": "detailed",
            "openai_store": False,
            "max_tokens": 65536,
        },
    }
    assert wizard.back()
    assert wizard.question.key == "environment"
    assert wizard.back()
    assert wizard.question is not None and wizard.question.key == "instructions"


@pytest.mark.anyio
async def test_inline_decision_keys_preserve_preexisting_draft(tmp_path: Path) -> None:
    """Real prompt-toolkit input dispatch, also runnable on Windows without a PTY."""
    ready = asyncio.Event()
    submitted = asyncio.Event()
    allow_decision = asyncio.Event()
    responses = []

    class Backend:
        thread_id = None
        resumed_transcript = None

        async def skill_catalog(self):
            return None

        def thinking_choices(self):
            return ()

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

    from a13n_harness_ui.interactive.attachments import AttachmentUpload
    from a13n_harness_ui.interactive.backend import SessionBackend

    release = asyncio.Event()

    async def reject(_argument):
        await release.wait()
        raise ValueError("Session does not exist")

    backend = Mock(spec=SessionBackend)
    backend.resume = AsyncMock(side_effect=reject)
    backend.interaction = AsyncMock(return_value=None)
    image = AttachmentUpload("image.png", b"test fixture", "image/png")
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest(), directory=tmp_path)
        shell.backend = backend
        shell.insert_attachments((image,))
        original_draft = shell.composer.buffer.document
        await shell.command(shell.registry.parse("/resume missing"))
        if newer_draft:
            shell.composer.buffer.document = Document("new draft")
        release.set()
        await shell.job
        assert shell.images == (() if newer_draft else (image,))
        assert shell.composer.text == ("new draft" if newer_draft else original_draft.text)
        if newer_draft:
            await shell.command(shell.registry.parse("/recover"))
            assert shell.composer.buffer.document == original_draft
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
    assert await backend.steer("new guidance") == "Guidance sent."
    backend.receipt_id = None
    with pytest.raises(ValueError, match="No running receipt"):
        await backend.steer("not sent")


@pytest.mark.anyio
async def test_rejected_steering_can_be_recovered_without_overwriting_newer_draft(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock, Mock

    from a13n_harness_ui.interactive.backend import SessionBackend

    entered, release = asyncio.Event(), asyncio.Event()

    async def reject(message, *, skill_references):
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
        from a13n_harness_ui.thread_files import ComposerInput

        backend.steer.assert_awaited_once_with(ComposerInput(("important guidance",)), skill_references=())


def test_cursor_edit_expands_paste_and_atomic_backspace_preserves_other_text(tmp_path: Path) -> None:
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest(), directory=tmp_path)
        original = "line\n" * 300
        marker = shell.pastes.insert(original)
        shell.composer.buffer.document = Document("prefix " + marker, len("prefix " + marker))
        assert shell.composer.text == "prefix " + marker
        shell.composer.buffer.cursor_position = len("prefix ") + 1
        assert shell.composer.text == "prefix " + original
        assert shell.composer.buffer.cursor_position == len("prefix ") + 1
        assert shell.pastes.expand(shell.composer.text) == "prefix " + original
        shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_long_paste_delete_replacement_and_undo_keep_payload(tmp_path: Path) -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest(), directory=tmp_path)
        # Exercise actual key dispatch without starting the App adapter lifecycle.
        task = asyncio.create_task(shell.app.run_async())
        try:
            async with asyncio.timeout(3):
                while not shell.app.is_running:
                    await asyncio.sleep(0.01)
            original = "source line\n" * 100
            pipe.send_text("\x1b[200~" + original + "\x1b[201~")
            await asyncio.sleep(0.1)
            marker = shell.composer.text
            assert marker.startswith("[Pasted text")
            pipe.send_text("\x7f")
            await asyncio.sleep(0.1)
            assert shell.composer.text == ""
            pipe.send_text("\x1b[200~replacement\x1b[201~")
            await asyncio.sleep(0.1)
            pipe.send_text("\x1f\x1f")
            await asyncio.sleep(0.1)
            assert shell.composer.text == marker
            assert shell.pastes.expand(shell.composer.text) == original
            pipe.send_text("\x1be")
            await asyncio.sleep(0.1)
            assert shell.composer.text == original
            pipe.send_text("\x1f")
            await asyncio.sleep(0.1)
            assert shell.composer.text == marker
            assert shell.pastes.expand(shell.composer.text) == original
        finally:
            if shell.app.is_running:
                shell.app.exit()
            await task
            shell.renderer.transcript.close()


def _shell_approval(**changes) -> DecisionInteraction:
    request = ApprovalRequestView(
        request_id="shell-call",
        tool_name="shell_exec",
        arguments={"command": "rm report.txt", "cwd": "/workspace", "environment": {"TOKEN": "hidden-value"}},
        metadata={
            "a13n.harness.tool-approval": {"tool_id": "environment.shell_exec"},
            "a13n.harness.tool-review": {"risk": "high", "reason": "Deletes a report"},
        },
    ).model_copy(update=changes)
    return DecisionInteraction(DecisionBatchView(continuation_id="b" * 64, requests=(request,)))


def test_approval_panel_puts_reason_before_command_and_keeps_explicit_choices(monkeypatch: pytest.MonkeyPatch) -> None:
    interaction = _shell_approval()
    now = 10.0
    interaction.request_started = now
    monkeypatch.setattr("a13n_harness_ui.interactive.decisions.time.monotonic", lambda: now)
    text = interaction.prompt()
    assert interaction.prompt_kind == "approval"
    assert text.index("Risk: high") < text.index("Reason: Deletes a report") < text.index("Command:")
    assert "Working directory: /workspace" in text
    assert "TOKEN" in text and "hidden-value" not in text
    assert "1. Approve once" in text and "2. Deny" in text and "3. Deny with reason" in text
    assert "Details: /review shell-call" in text
    selection = interaction.selection()
    assert selection is not None and selection.cursor == -1
    with pytest.raises(ValueError):
        interaction.accept("")
    now += interaction.timeout_seconds
    assert interaction.expired
    response = interaction.expire()
    assert isinstance(response, ThreadDeferredResponse)
    assert not response.responses[0].approved


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "a13n.harness.tool-approval": {"tool_id": "environment.shell_exec"},
            "reason": "Tool review could not complete.",
        },
        {"policy": "Operator confirmation required"},
    ],
)
def test_approval_panel_handles_missing_assessment_and_generic_metadata(metadata) -> None:
    text = _shell_approval(metadata=metadata).prompt()
    assert "Risk: high" not in text and "Deletes a report" not in text
    if "policy" in metadata:
        assert "Operator confirmation required" in text
    else:
        assert "Tool review could not complete." in text


def test_approval_panel_discloses_bounded_and_source_omissions() -> None:
    interaction = _shell_approval(arguments={"command": "echo line\n" * 1000}, metadata_omitted=True)
    text = interaction.prompt()
    assert len(text) < 8192
    assert "Preview truncated" in text and "/review shell-call" in text
    assert "Reason: Deletes a report" in text
    assert "No automatic approval" in text


@pytest.mark.parametrize("width", [24, 100])
def test_approval_panel_renders_untrusted_reason_as_literal_terminal_safe_text(width) -> None:
    from a13n_harness_ui.interactive.rendering import Status, StreamRenderer

    interaction = _shell_approval(
        metadata={
            "a13n.harness.tool-approval": {"tool_id": "environment.shell_exec"},
            "a13n.harness.tool-review": {
                "risk": "high",
                "reason": "[red]reason[/red]\x1b[2J",
            },
        }
    )
    renderer = StreamRenderer(Status())
    try:
        renderer.append(interaction.display_prompt(), kind=interaction.prompt_kind)
        renderer.transcript.render(width)
        block = next(iter(renderer.transcript.blocks.values()))
        rendered = "\n".join("".join(text for _, text in line) for line in block.rows)
        assert "\x1b" not in block.source
        assert "[red]" in rendered and "[/red]" in rendered
        assert "Approval" in rendered
        assert block.kind == "approval"
    finally:
        renderer.transcript.close()


@pytest.mark.parametrize("arguments", ["raw argument", ["one", "two"], 0, False])
def test_generic_approval_panel_preserves_non_object_arguments(arguments) -> None:
    import json

    text = _shell_approval(arguments=arguments, metadata=None).prompt()
    assert json.dumps(arguments, ensure_ascii=False, indent=2) in text
    assert "Shell review" not in text


def test_approval_panel_handles_omitted_arguments_without_losing_reason_or_choices() -> None:
    text = _shell_approval(arguments=None, arguments_omitted=True).prompt()
    assert "Reason: Deletes a report" in text
    assert "/review shell-call" in text and "Approve once" not in text
    assert "1. Deny" in text and "Approval is unavailable" in text


@pytest.mark.parametrize("theme_name", ["dark", "light"])
def test_approval_panel_uses_styled_sections_and_code_not_a_metadata_dump(theme_name) -> None:
    import json

    from a13n_harness_ui.interactive.approvals import approval_panel
    from a13n_harness_ui.interactive.theme import resolve_theme
    from rich.console import Group
    from rich.syntax import Syntax
    from rich.text import Text

    metadata = {
        "a13n.harness.tool-approval": {"tool_id": "environment.shell_exec"},
        "a13n.harness.tool-review": {"risk": "high", "reason": "Deletes a report"},
        "a13n.harness.invocation-policy": {"decision": "allow", "metadata": {"token": "details-only"}},
    }
    interaction = _shell_approval(metadata=metadata)
    source = interaction.display_prompt()
    assert "details-only" not in source and "invocation-policy" not in source
    assert json.loads(source)["reason"] == "Deletes a report"
    panel = approval_panel(source, resolve_theme(theme_name))
    assert isinstance(panel.renderable, Group)
    sections = list(panel.renderable.renderables)
    code = [part for part in sections if isinstance(part, Syntax)]
    assert len(code) == 1 and code[0].code == "rm report.txt"
    risk = next(part for part in sections if isinstance(part, Text) and part.plain.startswith("Risk "))
    assert "HIGH" in risk.plain and any("bold" in str(span.style) for span in risk.spans)
    assert not any(isinstance(part, Text) and "Approve once" in part.plain for part in sections)
    assert interaction.request.metadata == metadata


def test_shared_approval_reason_renders_without_an_empty_shell_risk_row() -> None:
    from a13n_harness_ui.interactive.approvals import approval_panel
    from a13n_harness_ui.interactive.theme import resolve_theme
    from rich.console import Group
    from rich.text import Text

    interaction = _shell_approval(
        metadata={
            "a13n.harness.tool-approval": {"requested_sources": ["reviewer"]},
            "a13n.harness.invocation-policy": {"metadata": {"token": "details-only"}},
            "reason": "Confirm the export destination",
        }
    )
    assert "Reason: Confirm the export destination" in interaction.prompt()
    assert "details-only" not in interaction.display_prompt()
    panel = approval_panel(interaction.display_prompt(), resolve_theme("dark"))
    assert isinstance(panel.renderable, Group)
    text = [item.plain for item in panel.renderable.renderables if isinstance(item, Text)]
    assert "Approval reason" in text
    assert not any(item.startswith("Risk ") for item in text)
    assert any("Confirm the export destination" in item for item in text)


def test_uniform_interaction_timeout_accepts_legacy_question_setting():
    from a13n_harness_ui.configuration.models import ToolsConfiguration

    config = ToolsConfiguration.model_validate({"ask_user_question_timeout_seconds": 30})
    assert config.interaction_timeout_seconds == 30
    assert config.model_dump()["interaction_timeout_seconds"] == 30


def test_shell_approval_uses_unified_risk_evidence_best_effort():
    interaction = _shell_approval(
        metadata={
            "reason": "Removes a report",
            "a13n.harness.tool-approval": {"tool_id": "environment.shell_exec"},
            "a13n.harness.tool-review": {"risk": "high", "reason": "Removes a report"},
        }
    )
    text = interaction.prompt()
    assert "Risk: high" in text
    assert "Removes a report" in text


@pytest.mark.parametrize("choice, approved", [("1", True), ("2", False), ("approve", True), ("deny", False)])
def test_approval_action_choices(choice, approved):
    interaction = _shell_approval()
    response = interaction.accept(choice)
    assert isinstance(response, ThreadDeferredResponse)
    assert response.responses == (ApprovalDecision(request_id="shell-call", approved=approved),)


def test_denial_reason_editor_never_interprets_reason_as_approval():
    interaction = _shell_approval()
    started = interaction.request_started
    assert interaction.accept("3") is None
    assert interaction.editor == "reason" and interaction.selection() is None
    assert interaction.request_started == started
    with pytest.raises(ValueError, match="denial reason"):
        interaction.accept(" ")
    assert interaction.back()
    assert interaction.selection().cursor == -1
    assert interaction.request_started == started
    assert interaction.accept("deny with reason") is None
    response = interaction.accept("approve")
    assert isinstance(response, ThreadDeferredResponse)
    assert response.responses == (ApprovalDecision(request_id="shell-call", approved=False, denial_message="approve"),)


def _external_interaction():
    from a13n_harness_ui.surfaces import ExternalRequestView

    return DecisionInteraction(
        DecisionBatchView(
            continuation_id="c" * 64,
            requests=(ExternalRequestView(request_id="external-call", tool_name="external_tool", arguments={}),),
        )
    )


def test_external_action_opens_result_editor_without_inventing_execution():
    interaction = _external_interaction()
    assert [choice.label for choice in interaction.selection().choices] == [
        "Provide result",
        "Deny",
        "Deny with reason",
    ]
    with pytest.raises(ValueError):
        interaction.accept("approve")
    assert interaction.accept("1") is None
    assert interaction.editor == "result" and not interaction.responses
    with pytest.raises(ValueError):
        interaction.accept("not JSON")
    assert interaction.editor == "result" and not interaction.responses
    response = interaction.accept("1")  # A numeric result is data only after opening the editor.
    assert isinstance(response, ThreadDeferredResponse)
    assert response.responses == (ExternalToolResult(request_id="external-call", result=1),)


@pytest.mark.parametrize("external", [False, True])
def test_timeout_while_editing_denies_without_resetting_clock(external, monkeypatch):
    interaction = _external_interaction() if external else _shell_approval()
    now = 10.0
    interaction.request_started = now
    monkeypatch.setattr("a13n_harness_ui.interactive.decisions.time.monotonic", lambda: now)
    assert interaction.accept("1" if external else "3") is None
    assert interaction.request_started == now
    now += interaction.timeout_seconds
    response = interaction.accept('{"ok": true}' if external else "too late")
    assert isinstance(response, ThreadDeferredResponse)
    item = response.responses[0]
    assert item.denied if external else not item.approved
    assert "timed out" in item.denial_message


@pytest.mark.anyio
@pytest.mark.parametrize("external", [False, True])
async def test_terminal_decision_editor_keyboard_back_validation_and_submit(tmp_path, external, monkeypatch):
    from types import SimpleNamespace

    async def execute(*args, **kwargs):
        return ""

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest(), directory=tmp_path)
        shell.ready = True
        shell.backend = SimpleNamespace(execute=execute, thread_id=None)
        shell.app.timeoutlen = 0.05
        submitted = []

        def launch(operation, **kwargs):
            operation.close()
            submitted.append(True)

        monkeypatch.setattr(shell, "launch", launch)
        shell.composer.text = "saved user draft"
        shell._save_draft()
        shell.interaction = _external_interaction() if external else _shell_approval()
        interaction = shell.interaction
        shell.selection = interaction.selection()
        shell._emit_decision()
        task = asyncio.create_task(shell.app.run_async())
        try:
            async with asyncio.timeout(3):
                while not shell.app.is_running:
                    await asyncio.sleep(0.01)
            # Down from the unselected cursor chooses the first action. Approval
            # denial-with-reason is the third action; external result is the first.
            pipe.send_text("\x1b[B" * (1 if external else 3) + "\r")
            await asyncio.sleep(0.1)
            assert shell.selection is None
            assert not shell.selector_focused
            assert interaction.editor == ("result" if external else "reason")
            started = interaction.request_started
            pipe.send_text("\x1b")
            await asyncio.sleep(0.6)
            assert interaction.editor is None
            assert shell.selection.cursor == -1
            assert interaction.request_started == started
            pipe.send_text(("1" if external else "3") + "\r")
            await asyncio.sleep(0.1)
            if external:
                pipe.send_text("invalid JSON\r")
                await asyncio.sleep(0.1)
                assert shell.composer.text == "invalid JSON"
                assert not submitted
                shell.composer.text = ""
            pipe.send_text(('{"ok": true}' if external else "Do not remove this report") + "\r")
            await asyncio.sleep(0.1)
            assert submitted == [True]
            assert shell.interaction is None
            assert shell.composer.text == "saved user draft"
            item = interaction.responses[0]
            if external:
                assert item.result == {"ok": True} and not item.denied
            else:
                assert not item.approved and item.denial_message == "Do not remove this report"
        finally:
            if shell.app.is_running:
                shell.app.exit()
            await task
            shell.renderer.transcript.close()


@pytest.mark.parametrize("answer", ["approve", "yes", "y", "edit arguments"])
def test_omitted_approval_cannot_be_authorized_by_text_alias(answer) -> None:
    interaction = _shell_approval(arguments=None, arguments_omitted=True)
    with pytest.raises(ValueError):
        interaction.accept(answer)
    assert interaction.responses == []
    selection = interaction.selection()
    assert selection is not None
    assert [choice.value for choice in selection.choices] == ["deny", "deny with reason"]
    response = interaction.accept("1")
    assert isinstance(response, ThreadDeferredResponse)
    assert isinstance(response.responses[0], ApprovalDecision)
    assert response.responses[0].approved is False


def test_terminal_generic_approval_reads_presentation_without_shell_label() -> None:
    from a13n_harness_ui.interactive.approvals import approval_panel
    from a13n_harness_ui.interactive.theme import resolve_theme
    from rich.console import Console

    interaction = _shell_approval(
        tool_name="publish",
        arguments={"environment": {"TOKEN": "not-for-display"}},
        metadata={
            "a13n.harness.tool-approval": {"tool_id": "custom.publish", "binding": "private-binding"},
            "a13n.harness.approval-presentation": {
                "target": "staging",
                "risk": "medium",
                "reason": "[red]Needs confirmation[/red]",
            },
        },
    )
    source = interaction.display_prompt()
    assert "private-binding" not in source and "not-for-display" not in source
    console = Console(width=100, record=True)
    with console.capture() as captured:
        console.print(approval_panel(source, resolve_theme("dark")))
    rendered = captured.get()
    assert "Shell review" not in rendered
    assert "staging" in rendered and "MEDIUM" in rendered
    assert "[red]Needs confirmation[/red]" in rendered


def test_terminal_argument_override_validates_json_and_keeps_timeout() -> None:
    interaction = _shell_approval(metadata=None, override_allowed=True)
    started = interaction.request_started
    assert interaction.accept("4") is None
    assert interaction.editor == "arguments"
    for value in ("", "[]", "null", "broken"):
        with pytest.raises(ValueError):
            interaction.accept(value)
    assert interaction.responses == []
    assert interaction.back()
    assert interaction.request_started == started
    interaction.accept("edit arguments")
    response = interaction.accept('{"command":"echo reviewed"}')
    assert isinstance(response, ThreadDeferredResponse)
    assert isinstance(response.responses[0], ApprovalDecision)
    assert response.responses[0].approved
    assert response.responses[0].override_arguments == {"command": "echo reviewed"}


def test_terminal_bound_approval_cannot_enter_override_editor() -> None:
    interaction = _shell_approval(override_allowed=False)
    assert "Approve with edited arguments" not in interaction.prompt()
    with pytest.raises(ValueError, match="does not allow"):
        interaction.accept("edit arguments")
    assert interaction.editor is None and not interaction.responses


@pytest.mark.parametrize("as_json", [False, True])
def test_external_preview_hides_environment_values_without_mutating_request(as_json) -> None:
    import json

    from a13n_harness_ui.surfaces import ExternalRequestView

    arguments = {"environment": {"TOKEN": "hidden-external-value"}}
    original = json.dumps(arguments) if as_json else arguments
    request = ExternalRequestView(request_id="external", tool_name="lookup", arguments=original)
    interaction = DecisionInteraction(DecisionBatchView(continuation_id="b" * 64, requests=(request,)))
    assert "hidden-external-value" not in interaction.prompt()
    assert "TOKEN" in interaction.prompt()
    assert request.arguments == original
