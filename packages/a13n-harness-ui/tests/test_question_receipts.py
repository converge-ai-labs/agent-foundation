"""Question receipts follow backend evidence across deferred response Runs."""

from __future__ import annotations

import json
from functools import partial

import pytest
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.interactive.theme import resolve_theme
from a13n_harness_ui.surfaces import QuestionOptionView, QuestionView, StructuredQuestionRequestView
from prompt_toolkit.utils import get_cwidth

from .terminal_display_fixtures import present_native_result, present_tool


@pytest.fixture
def renderer():
    value = StreamRenderer(Status())
    yield value
    value.transcript.close()


def request(call_id="question-1", *, multiple=False):
    return StructuredQuestionRequestView(
        request_id=call_id,
        tool_name="ask_user_question",
        questions=(
            QuestionView(
                header="Language",
                question="Which language should we use?",
                options=(
                    QuestionOptionView(label="Python", description="Python implementation"),
                    QuestionOptionView(label="Rust", description="Rust implementation"),
                ),
                multi_select=multiple,
            ),
        ),
    )


def render(renderer, *, detailed=False, width=120):
    renderer.transcript.detailed = detailed
    renderer.transcript.dirty = True
    renderer.transcript.render(width)
    return "\n".join("".join(text for _, text in row) for row in renderer.transcript.rows).strip()


def result(renderer, value, *, native=False, outcome="success", call_id="question-1", run_id="response-run"):
    if native:
        present_native_result(
            renderer,
            {
                "part_kind": "tool-return",
                "tool_name": "ask_user_question",
                "tool_call_id": call_id,
                "content": value,
                "outcome": outcome,
            },
            run_id=run_id,
        )
    else:
        present_tool(renderer, call_id, run_id=run_id, result=json.dumps(value))


def streamed_request(renderer, pending, *, run_id="question-run"):
    present_tool(
        renderer,
        pending.request_id,
        run_id=run_id,
        name=pending.tool_name,
        arguments="",
        arguments_complete=False,
        status="pending",
    )
    present_tool(
        renderer,
        pending.request_id,
        run_id=run_id,
        arguments=json.dumps({"questions": [item.model_dump() for item in pending.questions]}),
    )
    present_tool(renderer, pending.request_id, run_id=run_id, arguments_complete=True)


def test_registration_and_run_finish_do_not_claim_an_answer(renderer):
    renderer.register_questions(request())
    renderer.ingest_control("RUN_FINISHED", {}, run_id="question-run")
    assert render(renderer) == ""
    assert renderer.drain() == ""


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("legacy_windows", [False, True])
def test_typed_question_survives_fresh_response_run(renderer, native, legacy_windows, monkeypatch):
    from a13n_harness_ui.interactive import transcript as module

    monkeypatch.setattr(module, "Console", partial(module.Console, legacy_windows=legacy_windows))
    renderer.register_questions(request())
    renderer.ingest_control("RUN_FINISHED", {}, run_id="question-run")
    result(renderer, {"answers": {request().questions[0].question: "Python"}}, native=native)
    concise = render(renderer)
    assert "Questions" in concise
    top_left, bottom_left = ("┌", "└") if legacy_windows else ("╭", "╰")
    assert concise.startswith(top_left) and concise.splitlines()[-1].startswith(bottom_left)
    assert "Answered · Language" in concise and "Which language should we use?" in concise
    assert "[x] Python" in concise and "[ ] Rust" in concise
    assert "→ Python" not in concise
    assert not any(word in concise for word in ("Call tool", "question-1", "ask_user_question", "returned"))
    details = render(renderer, detailed=True)
    assert "Arguments | question-1" in details
    assert '"answers"' in details and "Python implementation" in details
    assert render(renderer) == concise


@pytest.mark.parametrize("first_native", [False, True])
def test_reduced_result_replacement_keeps_one_receipt_and_details(renderer, first_native):
    renderer.register_questions(request())
    answer = {"answers": {request().questions[0].question: "Python"}}
    result(renderer, answer, native=first_native)
    result(renderer, answer, native=not first_native)
    result(renderer, answer, native=first_native)
    assert len(renderer.transcript.blocks) == 1
    assert render(renderer).count("Answered · Language") == 1
    assert renderer.drain().count("Answered · Language") == 1
    details = render(renderer, detailed=True)
    assert details.count("Tool result") == 1
    assert "Tool result | returned" in details


def test_history_tool_arguments_register_without_a_live_decision(renderer):
    pending = request(multiple=True)
    streamed_request(renderer, pending)
    assert render(renderer) == ""
    result(renderer, {"answers": {pending.questions[0].question: ["Python", "Rust"]}})
    assert "[x] Python" in render(renderer) and "[x] Rust" in render(renderer)
    assert not renderer._tools


@pytest.mark.parametrize("outcome", ["denied", "failed"])
def test_native_failure_is_not_an_answer_even_with_valid_answer_content(renderer, outcome):
    renderer.register_questions(request())
    answer = {"answers": {request().questions[0].question: "Python"}}
    result(renderer, answer, native=True, outcome=outcome)
    # A duplicate projection must not replace the authoritative native failure.
    result(renderer, answer)
    assert "Not answered · Language" in render(renderer)
    assert "Answered" not in render(renderer)
    assert "[x]" not in render(renderer)
    assert "[ ] Python" in render(renderer) and "[ ] Rust" in render(renderer)
    assert "Call " not in render(renderer)
    assert f"Tool result | {outcome}" in render(renderer, detailed=True)


def test_timeout_diagnostic_survives_without_claiming_success(renderer):
    renderer.register_questions(request())
    result(renderer, "Question timed out; no user answer or approval was received.", native=True, outcome="failed")
    concise = render(renderer)
    assert "Not answered · Language" in concise
    assert "timed out" in concise and "no user answer or approval" in concise
    assert "Answered" not in concise


@pytest.mark.parametrize("value", [{"answers": {}}, {"ok": True}, {"answers": {"unknown": "Python"}}])
def test_recognized_malformed_result_never_claims_an_answer(renderer, value):
    renderer.register_questions(request())
    result(renderer, value)
    assert "Not answered · Language" in render(renderer)
    assert "Result unavailable" in render(renderer)
    assert "Answered" not in render(renderer)


def test_unknown_result_shape_remains_generic(renderer):
    answer = {"answers": {request().questions[0].question: "Python"}}
    result(renderer, answer)
    assert render(renderer) == "Call tool"
    assert "Answered" not in render(renderer)


def test_unknown_named_question_without_valid_request_remains_generic(renderer):
    result(renderer, {"answers": {"question": "answer"}}, native=True)
    assert render(renderer) == "Call ask_user_question"


def test_equal_question_text_does_not_correlate_distinct_call_ids(renderer):
    renderer.register_questions(request("question-1"))
    result(renderer, {"answers": {request().questions[0].question: "Python"}}, call_id="question-2")
    assert render(renderer) == "Call tool"


def test_replayed_detailed_call_reuses_block_and_remains_concise(renderer):
    renderer.status.mode = "detailed"
    streamed_request(renderer, request())
    assert "Call ask_user_question" not in render(renderer)
    result(renderer, {"answers": {request().questions[0].question: "Python"}})
    assert len(renderer.transcript.blocks) == 1
    assert "Answered · Language" in render(renderer)


def test_general_response_and_multiple_questions_are_readable(renderer):
    pending = request()
    second = pending.questions[0].model_copy(update={"header": "Scope", "question": "Which components?"})
    pending = pending.model_copy(update={"questions": (*pending.questions, second)})
    renderer.register_questions(pending)
    result(renderer, {"answers": {}, "response": "Use the existing implementation."})
    concise = render(renderer, width=32)
    assert "Answered · Language" in concise and "Answered · Scope" in concise
    assert concise.count("→ Use the existing") == 2
    assert "implementation." in concise


@pytest.mark.parametrize("theme", ["dark", "light", "auto"])
@pytest.mark.parametrize("width", [24, 40, 100])
def test_question_panel_wraps_options_and_preserves_selection(renderer, theme, width):
    renderer.transcript.theme = resolve_theme(theme)
    original = request()
    question = original.questions[0].model_copy(
        update={
            "header": "版本确认",
            "question": "Which version should we use?",
            "options": (
                QuestionOptionView(label="新版" * 20, description="New version"),
                QuestionOptionView(label="Stable", description="Keep the current version"),
            ),
        }
    )
    renderer.register_questions(original.model_copy(update={"questions": (question,)}))
    result(renderer, {"answers": {question.question: question.options[0].label}})
    concise = render(renderer, width=width)
    assert "Questions" in concise and "[x]" in concise and "[ ] Stable" in concise
    assert concise.count("新") == 20 and concise.count("版") == 21
    assert all(get_cwidth(line) <= width for line in concise.splitlines())
    assert len(renderer.transcript.blocks) == 1
    selected_styles = [style for row in renderer.transcript.rows for style, text in row if "[x]" in text]
    assert selected_styles and all("bold" in style for style in selected_styles)


def test_question_correlation_is_root_only(renderer):
    renderer.register_questions(request())
    present_tool(
        renderer,
        "question-1",
        child=True,
        run_id="child-run",
        result=json.dumps({"answers": {request().questions[0].question: "Python"}}),
    )
    assert render(renderer) == ""


def test_question_cache_is_bounded_and_reregistration_preserves_deduplication(renderer):
    for index in range(130):
        renderer.register_questions(request(f"question-{index}"))
    assert len(renderer._questions) == 128
    pending = request("question-129")
    answer = {"answers": {pending.questions[0].question: "Python"}}
    result(renderer, answer, call_id=pending.request_id)
    renderer.register_questions(pending)
    result(renderer, answer, call_id=pending.request_id)
    assert render(renderer).count("Answered · Language") == 1


def test_receipt_payload_is_literal_and_control_sequences_are_removed(renderer):
    renderer.register_questions(request())
    result(renderer, {"answers": {request().questions[0].question: "[bold]literal[/bold]\x1b[31m"}})
    concise = render(renderer)
    assert "[bold]literal[/bold]" in concise
    assert "[x]" not in concise
    assert "\x1b" not in concise


def test_native_retry_is_not_an_answer(renderer):
    renderer.register_questions(request())
    present_native_result(
        renderer,
        {
            "part_kind": "retry-prompt",
            "tool_name": "ask_user_question",
            "tool_call_id": "question-1",
            "content": "Answer validation failed.",
        },
    )
    assert "Not answered · Language" in render(renderer)
    assert "Answer validation failed." in render(renderer)
    assert "Answered" not in render(renderer)


def test_unknown_explicit_tool_name_does_not_match_registered_question(renderer):
    renderer.register_questions(request())
    present_tool(
        renderer,
        "question-1",
        result=json.dumps({"answers": {request().questions[0].question: "Python"}}),
        name="other_tool",
    )
    assert render(renderer) == "Call other_tool"


def test_question_arguments_obey_aggregate_cache_budget(renderer):
    renderer.transcript.max_bytes = 1024
    for index in range(8):
        streamed_request(renderer, request(f"question-{index}"))
    assert sum(len(item.arguments.encode()) for item in renderer._questions.values()) <= 1024


@pytest.mark.parametrize("first_outcome", ["failed", "success"])
def test_explicit_response_retry_has_its_own_receipt_after_publication_failure(renderer, first_outcome):
    pending = request()
    renderer.register_questions(pending)
    original = {"answers": {pending.questions[0].question: "Python"}}
    changed = {"answers": {pending.questions[0].question: "Rust"}}
    result(renderer, original, native=True, outcome=first_outcome, run_id="attempt-one")
    # App still exposes the suspended continuation after publication failed.
    renderer.register_questions(pending)
    result(renderer, changed, native=True, run_id="attempt-two")
    result(renderer, changed, run_id="attempt-two")
    # Replayed observations of either attempt must not add or overwrite receipts.
    result(renderer, original, native=True, outcome=first_outcome, run_id="attempt-one")
    result(renderer, changed, native=True, run_id="attempt-two")
    assert len(renderer.transcript.blocks) == 2
    newest = next(reversed(renderer.transcript.blocks.values()))
    assert newest.preview == "Answered · Language\nWhich language should we use?\n  [ ] Python\n  [x] Rust"
    assert render(renderer).count("[x] Rust") == 1
