from __future__ import annotations

import pytest
from a13n_harness import HarnessState
from a13n_harness_ui.display_history import (
    DisplayHistory,
    DisplayHistoryCollector,
    saved_display_history,
    with_display_history,
)
from a13n_harness_ui.thread_projection import _transcript_turns
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextContent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def input_message(text: str, source: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart([TextContent(text, metadata={"source_id": source})])])


def test_only_successful_saved_completion_marks_final_and_survives_reload():
    messages = [
        input_message("Question", "input-1"),
        ModelResponse(parts=[TextPart("Progress"), ToolCallPart("read", {})]),
        ModelRequest(parts=[ToolReturnPart("read", "ok")]),
        ModelResponse(parts=[ThinkingPart("Plan"), TextPart("First part"), TextPart("Second part")]),
    ]
    collector = DisplayHistoryCollector([])
    assert collector.capture(messages).completed_responses == ()
    completed = collector.capture(messages, completed=True)
    assert completed.completed_responses == (3,)
    state = with_display_history(HarnessState.new(message_history=messages), completed)
    reopened = saved_display_history(HarnessState.model_validate_json(state.model_dump_json()))
    assert reopened is not None
    assert _transcript_turns(reopened.messages, reopened.completed_responses)[0].final_position == 3
    assert collector.capture(messages, completed=True).completed_responses == (3,)
    # Further work without another ordinary input stays in the same turn, but
    # its old final is not evidence that the new work completed.
    more = collector.capture([*messages, ModelRequest(parts=[ToolReturnPart("read", "resume")])])
    assert _transcript_turns(more.messages, more.completed_responses)[0].final_position is None


def test_steering_context_and_hidden_input_do_not_start_turns():
    messages = (
        input_message("Same input", "first"),
        ModelResponse(parts=[TextPart("Progress")]),
        ModelRequest(parts=[UserPromptPart("Steering")], metadata={"a13n.steering-run": "run-1"}),
        ModelRequest(parts=[UserPromptPart("Summary")], metadata={"a13n.context": "handoff"}),
        ModelRequest(parts=[UserPromptPart([TextContent("Internal", metadata={"display": False})])]),
        ModelResponse(parts=[TextPart("Answer")]),
        input_message("Same input", "second"),
        ModelResponse(parts=[TextPart("Next answer")]),
    )
    turns = _transcript_turns(messages, (5, 7))
    assert [(turn.turn_id, turn.input_position, turn.end_position, turn.final_position) for turn in turns] == [
        ("first", 0, 6, 5),
        ("second", 6, 8, 7),
    ]
    assert turns[0].steering_count == 1
    assert _transcript_turns(messages)[0].final_position is None


def test_multiple_parts_and_attachment_only_input_are_single_turns():
    message = ModelRequest(
        parts=[
            UserPromptPart(
                [
                    TextContent("First", metadata={"source_id": "multipart"}),
                    TextContent("Second", metadata={"source_id": "multipart"}),
                ]
            ),
            UserPromptPart([TextContent("hidden", metadata={"display": False})]),
        ]
    )
    attachment = ModelRequest(
        parts=[
            UserPromptPart(
                [
                    TextContent(
                        "Internal attachment path",
                        metadata={"source_id": "attachment", "harness_ui": {"attachment": {"name": "image.png"}}},
                    )
                ]
            )
        ]
    )
    turns = _transcript_turns((message, attachment))
    assert [(turn.turn_id, turn.preview) for turn in turns] == [
        ("multipart", "First Second"),
        ("attachment", "image.png"),
    ]


@pytest.mark.parametrize(
    "response",
    [
        ModelResponse(parts=[TextPart("Partial")], state="suspended"),
        ModelResponse(parts=[TextPart("Summary")], metadata={"keep": "compact"}),
    ],
)
def test_suspended_or_synthetic_responses_are_not_final(response):
    assert DisplayHistoryCollector([]).capture([response], completed=True).completed_responses == ()


def test_completion_preserves_legacy_envelope_and_never_changes_model_messages():
    old = DisplayHistory(messages=[input_message("Old", "old")])
    assert old.completed_responses == ()
    messages = [input_message("New", "new"), ModelResponse(parts=[TextPart("Answer")])]
    completed = DisplayHistoryCollector([]).capture(messages, completed=True)
    assert completed.model_dump().keys() == old.model_dump().keys()
    assert messages[-1].metadata is None
    assert DisplayHistory.model_validate_json(completed.model_dump_json()).completed_responses == (1,)


def test_legacy_closing_output_is_readable_without_claiming_success():
    turns = _transcript_turns(
        (
            input_message("Question", "first"),
            ModelResponse(parts=[TextPart("Progress"), ToolCallPart("read", {})]),
            ModelRequest(parts=[ToolReturnPart("read", "ok")]),
            ModelResponse(parts=[ThinkingPart("Private reasoning"), TextPart("First part"), TextPart("Second part")]),
            input_message("Next question", "second"),
        )
    )
    assert turns[0].final_position is None
    assert turns[0].output_position == 3
    assert turns[0].output_preview == "First part Second part"
    assert turns[1].output_position is None
    assert turns[1].output_preview is None


@pytest.mark.parametrize(
    "response",
    [
        ModelResponse(parts=[TextPart("Working"), ToolCallPart("read", {})]),
        ModelResponse(parts=[TextPart("Partial")], state="suspended"),
        ModelResponse(parts=[TextPart("Summary")], metadata={"keep": "compact"}),
        ModelResponse(parts=[ThinkingPart("No answer")]),
    ],
)
def test_intermediate_or_synthetic_output_is_not_a_closing_preview(response):
    turn = _transcript_turns((input_message("Question", "first"), response))[0]
    assert turn.output_position is None
    assert turn.output_preview is None


def test_completed_output_preview_is_bounded_and_excludes_reasoning():
    turn = _transcript_turns(
        (
            input_message("Question", "first"),
            ModelResponse(parts=[ThinkingPart("Private"), TextPart("Answer " * 200)]),
        ),
        (1,),
    )[0]
    assert turn.final_position == turn.output_position == 1
    assert turn.output_preview is not None
    assert len(turn.output_preview) <= 512
    assert turn.output_preview.startswith("Answer")
    assert "Private" not in turn.output_preview
