"""Question-specific rendering and real key dispatch, separate from generic selectors."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.decisions import DecisionInteraction
from a13n_harness_ui.interactive.questions import QuestionCard, wrap_question
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.surfaces import DecisionBatchView, QuestionOptionView, QuestionView, StructuredQuestionRequestView
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Point, Size
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import Layout
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth


class Output(DummyOutput):
    size = Size(rows=24, columns=80)

    def get_size(self):
        return self.size


def interaction(*, multiple=False, count=1, description="First paragraph\n\nSecond paragraph"):
    return DecisionInteraction(
        DecisionBatchView(
            continuation_id="a" * 64,
            requests=(
                StructuredQuestionRequestView(
                    request_id="question-call",
                    tool_name="ask_user_question",
                    questions=tuple(
                        QuestionView(
                            header=f"Choice {index}",
                            question=f"Which approach {index}?",
                            multi_select=multiple,
                            options=(
                                QuestionOptionView(label="First", description=description),
                                QuestionOptionView(label="Second", description="Other approach"),
                            ),
                        )
                        for index in range(count)
                    ),
                ),
            ),
        )
    )


@asynccontextmanager
async def running_card(*, multiple=False):
    answers = []
    cancelled = []
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=Output()):
        decision = interaction(multiple=multiple)
        card = QuestionCard(
            decision, decision.selection(), submit=answers.append, cancel=lambda: cancelled.append(True)
        )
        app = Application(
            layout=Layout(card.container, focused_element=card.control), key_bindings=card.bindings, full_screen=True
        )
        # A bare Escape otherwise waits 0.5 s for a longer input sequence and 1 s for a key chord.
        app.ttimeoutlen = app.timeoutlen = 0.05
        task = asyncio.create_task(app.run_async())
        try:
            async with asyncio.timeout(3):
                while not app.is_running:
                    await asyncio.sleep(0.01)
            with set_app(app):
                yield card, pipe, answers, cancelled
        finally:
            if app.is_running:
                app.exit()
            await task


async def keys(pipe, text):
    pipe.send_text(text)
    await asyncio.sleep(0.08)


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


def test_cell_wrapping_preserves_cjk_paragraphs_and_whitespace():
    text = "中文 mixed  words\n\n  another paragraph\n"
    rows = wrap_question(text, 12, indent="   ")
    assert all(get_cwidth(row) <= 12 for row in rows)
    assert "   " in rows
    assert "".join(row[3:] for row in rows) == text.replace("\n", "")
    assert rows[-1] == "   "


@pytest.mark.anyio
@pytest.mark.parametrize("newline", ["\n", "\x1b\r"], ids=["ctrl-j", "alt-enter"])
async def test_real_keys_require_explicit_confirmation_and_preserve_custom_draft(newline):
    async with running_card() as (card, pipe, answers, cancelled):
        await keys(pipe, "\r")
        assert not answers and card.error
        await keys(pipe, "2")
        assert card.selection.cursor == 1 and not answers
        await keys(pipe, "\tmy draft" + newline + "next line")
        assert card.editing and card.editor.text == "my draft\nnext line"
        await keys(pipe, "\t")
        assert not card.editing
        assert card.editor.text == "my draft\nnext line"
        await keys(pipe, "\x1b[A\r")
        assert answers == ["1"]  # A custom draft does not steal selection confirmation.
        await keys(pipe, "\t\r")
        assert answers[-1] == "my draft\nnext line"
        assert card.editing
        await keys(pipe, "\x1b")
        await until(lambda: not card.editing)
        assert not cancelled
        await keys(pipe, "\x1b")
        await until(lambda: cancelled)
        assert cancelled == [True]


@pytest.mark.anyio
async def test_editor_arrows_never_recall_history_at_multiline_boundaries():
    async with running_card() as (card, pipe, answers, _):
        card.editor.buffer.history.append_string("must not appear")
        await keys(pipe, "\t \x1b[A\x1b[B")
        assert card.editor.text == " "
        await keys(pipe, "\r")
        assert not answers
        card.editor.buffer.document = Document("one\ntwo", 0)
        await keys(pipe, "\x1b[A")
        assert card.editor.text == "one\ntwo" and card.editor.buffer.cursor_position == 0
        await keys(pipe, "\x1b[B\x1b[B")
        assert card.editor.text == "one\ntwo"
        assert card.editor.buffer.document.cursor_position_row == 1


@pytest.mark.anyio
async def test_multiselect_and_custom_action_do_not_submit_on_focus_or_space():
    async with running_card(multiple=True) as (card, pipe, answers, _):
        await keys(pipe, "1 \x1b[B ")
        assert card.selection.checked == {0, 1} and not answers
        await keys(pipe, "\r")
        assert answers == ["1,2"]
        await keys(pipe, "\x1b[B\r")
        assert card.editing and len(answers) == 1
        await keys(pipe, "custom\r")
        assert answers[-1] == "custom"


@pytest.mark.parametrize("size", [Size(rows=2, columns=20), Size(rows=6, columns=24), Size(rows=24, columns=100)])
def test_every_wrapped_row_reachable_on_short_narrow_and_resized_terminals(size):
    output = Output()
    output.size = size
    decision = interaction(description="超长说明 " * 150 + "\n\nlast detail")
    with create_app_session(output=output):
        card = QuestionCard(decision, decision.selection(), submit=lambda text: None, cancel=lambda: None)
        app = Application(layout=Layout(card.container), output=output)
        with set_app(app):
            all_rows = card.rows(size.columns)
            seen = []
            for top in range(len(all_rows)):
                card.top = top
                card.text()
                seen.extend(row.text for row in card._visible)
            assert all(row.text in seen for row in all_rows)
            assert all(get_cwidth(row.text) <= size.columns for row in all_rows)
            card.selection.cursor = 1
            card._follow_focus = True
            card.text()
            assert any(row.option == 1 for row in card._visible)
            output.size = Size(rows=4, columns=20)
            card.text()
            assert any(row.option == 1 for row in card._visible)


def test_wrapped_mouse_description_focus_and_wheel_do_not_answer():
    output = Output()
    output.size = Size(rows=12, columns=24)
    answers = []
    with create_app_session(output=output):
        decision = interaction(description="Long description " * 20)
        card = QuestionCard(decision, decision.selection(), submit=answers.append, cancel=lambda: None)
        app = Application(layout=Layout(card.container), output=output)
        with set_app(app):
            card.text()
            card.scroll(5)
            card.text()
            row = next(index for index, row in enumerate(card._visible) if row.option == 0)
            card.mouse(MouseEvent(Point(8, row), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
            assert card.selection.cursor == 0 and not answers
            card.mouse(MouseEvent(Point(8, row), MouseEventType.SCROLL_DOWN, MouseButton.NONE, frozenset()))
            assert card.selection.cursor == 0 and card.top == 8 and not answers


@pytest.mark.anyio
async def test_shell_card_replaces_composer_resets_new_question_and_restores_chat_draft():
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=Output()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace(thread_id=None, cancel=lambda: asyncio.sleep(0))
        shell.ready = True
        shell.composer.buffer.document = Document("saved ordinary draft", 6)
        shell.composer.buffer.history.append_string("old chat input")
        shell._save_draft()
        shell.interaction = interaction(count=2)
        shell.selection = shell.interaction.selection()
        shell._emit_decision()
        task = asyncio.create_task(shell.app.run_async())
        try:
            async with asyncio.timeout(3):
                while not shell.app.is_running:
                    await asyncio.sleep(0.01)
            assert shell.question_card is not None
            assert "1/2" in shell.interaction.title()
            assert not shell.renderer.transcript.blocks  # No duplicate System prompt.
            assert not any(window.content is shell.composer.control for window in shell.app.layout.visible_windows)
            await keys(pipe, "\tfirst answer\r")
            assert shell.interaction.question_index == 1
            assert not shell.question_card.editing
            assert shell.question_card.editor.text == ""
            assert shell.selection.cursor == -1
            await keys(pipe, "\x1b[B")
            assert shell.selection.cursor == 0
            assert shell.composer.text == ""
            await shell.cancel()
            assert shell.question_card is None and shell.interaction is None
            assert shell.composer.text == "saved ordinary draft"
            assert shell.composer.buffer.cursor_position == 6
        finally:
            shell.app.exit()
            await task
            shell.renderer.transcript.close()


@pytest.mark.anyio
@pytest.mark.parametrize("count", [1, 2])
async def test_answer_submission_leaves_no_collection_or_submitting_panels(monkeypatch, count):
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=Output()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace(execute=Mock(return_value=None))
        launch = Mock()
        monkeypatch.setattr(shell, "launch", launch)
        shell.composer.buffer.document = draft = Document("ordinary draft", cursor_position=3)
        shell._save_draft()
        shell.interaction = interaction(count=count)
        shell.selection = shell.interaction.selection()
        shell._emit_decision()
        try:
            for index in range(count):
                await shell.decision_answer("1")
                assert not shell.renderer.transcript.blocks
                if index < count - 1:
                    assert shell.interaction.question_index == index + 1
                    assert shell.question_card is not None
                    launch.assert_not_called()
            assert shell.interaction is None and shell.question_card is None
            assert shell.composer.buffer.document == draft
            shell.backend.execute.assert_called_once()
            response = shell.backend.execute.call_args.kwargs["response"]
            assert response is not None
            launch.assert_called_once_with(None, kind="run")
        finally:
            shell.renderer.transcript.close()


def test_single_question_heading_omits_progress():
    assert interaction().title() == "Choice 0"


@pytest.mark.anyio
@pytest.mark.parametrize("rows, columns", [(2, 20), (6, 24), (24, 100)])
@pytest.mark.parametrize("redraw_interval", [1 / 15, 0.2])
async def test_live_card_layout_keeps_scrollable_body_and_custom_editor(rows, columns, redraw_interval):
    output = Output()
    output.size = Size(rows=rows, columns=columns)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        shell = CliShell(CliRequest())
        shell.app.min_redraw_interval = redraw_interval
        shell.interaction = interaction(description="Many wrapped details " * 50)
        shell.selection = shell.interaction.selection()
        shell._emit_decision()
        card = shell.question_card
        task = asyncio.create_task(shell.app.run_async())
        try:
            async with asyncio.timeout(3):
                while card.window.render_info is None:
                    await asyncio.sleep(0.01)
            assert card.window.render_info.window_height == card.height().max
            assert shell.output_window.render_info.window_height >= 1
            await keys(pipe, "\x1b[6~")
            assert card.top > 0 and card.selection.cursor == -1
            await keys(pipe, "\ttext")
            # Key dispatch can finish before the throttled renderer shows the editor.
            async with asyncio.timeout(3):
                while card.editor.window.render_info is None:
                    await asyncio.sleep(0.01)
            assert card.editor.window.render_info.window_height >= 1
            assert card.window.render_info.window_height >= 1
            assert card.editor.text == "text"
        finally:
            shell.app.exit()
            await task
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_question_review_blocks_answers_but_keeps_local_commands_available():
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=Output()):
        shell = CliShell(CliRequest())
        shell.interaction = interaction(count=2)
        shell.selection = shell.interaction.selection()
        shell._emit_decision()
        shell.job = asyncio.create_task(asyncio.sleep(60))
        try:
            shell._submit_question("1")
            assert shell.interaction.question_index == 0 and shell._input_task is None
            assert "Wait" in shell.question_card.error
            shell._submit_question("/help")
            await shell._input_task
            assert shell.interaction.question_index == 0
            assert any("Command accepted: /help" in block.source for block in shell.renderer.transcript.blocks.values())
        finally:
            shell.job.cancel()
            await asyncio.gather(shell.job, return_exceptions=True)
            shell.renderer.transcript.close()
