from __future__ import annotations

import pytest
from a13n_harness_ui.display_history import DisplayHistory, import_display_history
from a13n_harness_ui.display_projection import display_turns
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


def test_app_presentations_are_turn_boundaries_independent_of_execution_pages():
    from a13n_harness_ui.mcp_apps.models import AppReference
    from a13n_harness_ui.mcp_apps.snapshots import METADATA_KEY

    reference = AppReference(
        app_id="app-one",
        thread_id="thread-one",
        run_id="run-one",
        tool_call_id="call-one",
        server_id="counter",
        tool_name="counter",
    )
    messages = (
        input_message("Show counter", "input-one"),
        ModelResponse(parts=[ToolCallPart("counter", {}, tool_call_id="call-one")]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "counter",
                    {"count": 1},
                    tool_call_id="call-one",
                    metadata={METADATA_KEY: [reference.model_dump(mode="json")]},
                )
            ]
        ),
        ModelResponse(parts=[TextPart("Counter is ready")]),
    )
    turn = _transcript_turns(messages, (3,))[0]
    assert turn.app_positions == (2,)
    assert turn.input_position == 0 and turn.output_position == 3


def test_only_successful_saved_completion_marks_final_and_survives_reload():
    messages = [
        input_message("Question", "input-1"),
        ModelResponse(parts=[TextPart("Progress"), ToolCallPart("read", {})]),
        ModelRequest(parts=[ToolReturnPart("read", "ok")]),
        ModelResponse(parts=[ThinkingPart("Plan"), TextPart("First part"), TextPart("Second part")]),
    ]
    imported = import_display_history("thread-one", messages)
    assert imported.completed == ()
    fold = DisplayHistory().start("run-one", resume=False)
    fold.fold(
        [
            {"type": "TEXT_MESSAGE_START", "messageId": "answer", "role": "assistant"},
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "answer", "delta": "Second part"},
            {"type": "TEXT_MESSAGE_END", "messageId": "answer"},
        ]
    )
    completed = DisplayHistory().capture(fold, completed=True)
    assert completed.completed == (completed.items[-1].id,)
    reopened = DisplayHistory.model_validate_json(completed.model_dump_json())
    assert reopened == completed
    # Further work without another ordinary input invalidates the old final.
    more = reopened.capture(reopened.start("run-two", resume=True))
    assert more.completed == ()


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
    imported = import_display_history("thread-one", [response])
    assert imported.completed == ()
    assert display_turns(imported) == ()


def test_import_never_claims_host_completion_or_changes_model_messages():
    messages = [input_message("New", "new"), ModelResponse(parts=[TextPart("Answer")])]
    imported = import_display_history("thread-one", messages)
    assert messages[-1].metadata is None
    reopened = DisplayHistory.model_validate_json(imported.model_dump_json())
    assert reopened.completed == ()
    assert display_turns(reopened)[0].output_preview == "Answer"


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


def test_additional_inputs_exclude_notifications_and_count_submissions_not_parts():
    steering = {"a13n.steering-run": "run-1"}
    multipart = ModelRequest(
        parts=[
            UserPromptPart(
                [
                    TextContent("Use this", metadata={"source_id": "multipart"}),
                    TextContent(
                        "Attachment path",
                        metadata={
                            "source_id": "multipart",
                            "harness_ui": {"attachment": {"name": "image.png"}},
                        },
                    ),
                ]
            ),
            UserPromptPart([TextContent("Hidden context", metadata={"display": False})]),
        ],
        metadata={**steering, "a13n.steering-input": "multipart"},
    )
    messages = [input_message("Question", "first"), multipart, multipart]
    for source in ("background_process", "async_subagent"):
        # Both current message-level metadata and retained content-only metadata work.
        messages.extend(
            [
                ModelRequest(parts=[UserPromptPart("Update")], metadata={**steering, "a13n.steering-source": source}),
                ModelRequest(
                    parts=[UserPromptPart([TextContent("Update", metadata={"a13n.steering-source": source})])],
                    metadata=steering,
                ),
            ]
        )
    messages.extend(
        [
            ModelRequest(parts=[UserPromptPart("Process finished; please continue")], metadata=steering),
            ModelRequest(
                parts=[UserPromptPart([TextContent("Hidden", metadata={"display": False})])], metadata=steering
            ),
            ModelRequest(parts=[UserPromptPart("Summary")], metadata={**steering, "a13n.context": "handoff"}),
            ModelRequest(
                parts=[
                    UserPromptPart(
                        [TextContent("File path", metadata={"harness_ui": {"attachment": {"name": "notes.txt"}}})]
                    )
                ],
                metadata=steering,
            ),
            ModelResponse(parts=[ToolCallPart("read", {})]),
            ModelResponse(parts=[TextPart("Answer")]),
        ]
    )
    turn = _transcript_turns(tuple(messages))[0]
    assert turn.steering_count == 3
    assert turn.tool_count == 1


def test_content_source_identity_deduplicates_additional_input_parts_across_requests():
    first = input_message("First part", "additional")
    second = input_message("Second part", "additional")
    first.metadata = second.metadata = {"a13n.steering-run": "run-1"}
    turn = _transcript_turns((input_message("Question", "first"), first, second))[0]
    assert turn.steering_count == 1
