from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from a13n_harness import HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities import CompactionCapability, CompactionPolicy, HandoffCapability
from a13n_harness.capabilities.context import _COMPACTION_PROMPT
from a13n_harness.model_context import user_prompt_content
from a13n_harness_ui.display_history import DisplayHistory, import_display_history
from a13n_harness_ui.display_projection import display_turns
from a13n_harness_ui.root_checkpoint import RootCheckpointCapability
from a13n_harness_ui.storage.contracts import StoredContinuation
from a13n_harness_ui.storage.objects import ObjectKind, ObjectRef
from pydantic import ValidationError
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


def visible(history: DisplayHistory) -> list[str]:
    return [
        str(item.content["text"])
        for item in history.items
        if "text" in item.content
        and not item.content.get("subagentRunId")
        and not (isinstance(metadata := item.content.get("metadata"), dict) and metadata.get("display") is False)
    ]


def continuation(state: HarnessState, display: DisplayHistory) -> StoredContinuation:
    return StoredContinuation(
        harness_release="test",
        harness_state=state,
        display_history=display,
        run_composition=ObjectRef(
            object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="a" * 64
        ),
        created_at=datetime.now(UTC),
    )


def test_display_is_a_versioned_sibling_not_native_capability_state() -> None:
    state = HarnessState.new(message_history=[ModelRequest(parts=[UserPromptPart("Input")])])
    display = import_display_history(state.thread_id, state.message_history)
    saved = continuation(state, display)
    decoded = StoredContinuation.model_validate_json(saved.model_dump_json())
    assert decoded.harness_state == state
    assert decoded.display_history == display
    assert "display-history" not in decoded.harness_state.model_dump_json()
    old = saved.model_dump(mode="json")
    old["schema_version"] = "1"
    with pytest.raises(ValidationError):
        StoredContinuation.model_validate(old)
    bad = display.model_dump(mode="json")
    bad["version"] = "future"
    with pytest.raises(ValidationError):
        DisplayHistory.model_validate(bad)


@pytest.mark.parametrize("kind", ["handoff", "compaction"])
async def test_display_history_survives_repeated_context_replacement_and_reload(kind: str) -> None:
    previous = HarnessState.new(
        message_history=[
            ModelRequest(parts=[UserPromptPart("Original user request")]),
            ModelResponse(parts=[TextPart("Original answer")], usage=RequestUsage(input_tokens=1000)),
        ]
    )
    display = import_display_history(previous.thread_id, previous.message_history)
    for round_number in range(2):
        fold = display.start(f"run-{round_number}", resume=False)
        snapshots: list[tuple[HarnessState, DisplayHistory]] = []
        requests = 0

        async def save(state: HarnessState, *, fold=fold, snapshots=snapshots, display=display) -> str:
            snapshots.append((state, display.capture(fold)))
            return f"checkpoint-{len(snapshots)}"

        async def model(
            messages: list[ModelMessage], info: AgentInfo, *, round_number=round_number
        ) -> AsyncIterator[str | DeltaToolCalls]:
            nonlocal requests
            if any(
                _COMPACTION_PROMPT in str(item.content)
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
                for item in user_prompt_content(part)
            ):
                yield "# Summary\n\n**Keep the decisions**"
                return
            requests += 1
            if kind == "handoff" and requests == 1:
                yield {
                    0: DeltaToolCall(
                        name="summarize",
                        json_args=json.dumps({"content": "**Keep the decisions**"}),
                        tool_call_id=f"summary-{round_number}",
                    )
                }
            else:
                yield f"Answer {round_number}"

        checkpoints = RootCheckpointCapability(save)
        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(
                checkpoints,
                HandoffCapability(),
                *((CompactionCapability(CompactionPolicy(trigger_tokens=1)),) if kind == "compaction" else ()),
            ),
        )
        async with executable.stream(
            f"Request {round_number}", bindings=RunBindings.embedded(), previous_state=previous
        ) as run:
            async for item in run:
                item = await checkpoints.consume(item)
                fold.fold(fold.events(item), source=item)
            result = run.result
        assert result is not None and result.state is not None
        result.output_or_raise()
        assert snapshots
        display = display.capture(fold, completed=True)
        text = visible(display)
        assert text.count("Original user request") == 1
        assert text.count("Original answer") == 1
        for number in range(round_number + 1):
            assert text.count(f"Request {number}") == 1
            assert text.count(f"Answer {number}") == 1
        summaries = [item for item in display.items if item.content.get("name") == f"a13n.context.{kind}_summary"]
        assert len(summaries) == round_number + 1
        assert not any(_COMPACTION_PROMPT in item for item in text)
        assert not any(
            isinstance(message, ModelResponse)
            and any(isinstance(part, TextPart) and part.content == "Original answer" for part in message.parts)
            for message in result.state.message_history
        )
        stored = StoredContinuation.model_validate_json(continuation(result.state, display).model_dump_json())
        previous, display = stored.harness_state, stored.display_history
        assert len(display_turns(display)) == round_number + 2
        assert display_turns(display)[-1].final_position is not None


def test_snapshots_are_detached_and_identical_inputs_keep_distinct_positions() -> None:
    original = DisplayHistory()
    fold = original.start("run", resume=False)
    for identity in ("a", "b"):
        fold.fold(
            [
                {
                    "type": "CUSTOM",
                    "name": "a13n.input.user",
                    "value": {
                        "event": {
                            "message_id": identity,
                            "input_id": identity,
                            "content": "same",
                            "source": "user",
                        }
                    },
                }
            ]
        )
    saved = original.capture(fold)
    fold.fold([{"type": "TEXT_MESSAGE_CONTENT", "messageId": "output", "delta": "x" * 300000}])
    updated = original.capture(fold, completed=True)
    assert visible(saved) == ["same", "same"]
    assert len(updated.items[-1].content["text"]) == 300000
    assert len(display_turns(updated)) == 2
    assert display_turns(updated)[-1].final_position is not None
    restored = DisplayHistory.model_validate_json(updated.model_dump_json())
    resumed = restored.start("resume", resume=True)
    assert resumed.run_id == "run"
    assert restored.capture(resumed).completed == ()
    assert updated.completed


async def test_deferred_tool_keeps_identity_and_sends_baseline_before_suffix() -> None:
    from a13n_stream_protocol.display import SetItem, apply_changes

    original = DisplayHistory()
    fold = original.start("run", resume=False)
    fold.fold(
        [
            {"type": "TOOL_CALL_START", "toolCallId": "call", "toolCallName": "edit"},
            {"type": "TOOL_CALL_ARGS", "toolCallId": "call", "delta": '{"file": "x"}'},
        ]
    )
    saved = original.capture(fold)
    resumed = saved.start("new-native-run", resume=True)
    events = resumed.fold([{"type": "TOOL_CALL_RESULT", "toolCallId": "call", "content": "ok"}])
    assert isinstance(events[0].changes[0], SetItem)
    client = {}
    apply_changes(client, events[0].changes)
    updated = saved.capture(resumed)
    assert len(updated.items) == 1
    assert updated.items[0].id == saved.items[0].id
    assert updated.items[0].ordinal == saved.items[0].ordinal
    assert updated.items[0].content["arguments"] == '{"file": "x"}'
    assert updated.items[0].state == "completed"
    assert saved.items[0].state == "in_progress"


@pytest.mark.parametrize("adjacent_requests", [False, True])
async def test_suspended_response_keeps_partial_display_on_resume(adjacent_requests: bool) -> None:
    previous = HarnessState.new(
        message_history=[
            *([ModelRequest(parts=[UserPromptPart("First")], run_id="old")] if adjacent_requests else []),
            ModelRequest(parts=[UserPromptPart("Continue")], run_id="old"),
            ModelResponse(parts=[TextPart("partial ")], state="suspended", run_id="old"),
        ]
    )
    display = import_display_history(previous.thread_id, previous.message_history)
    fold = display.start("new", resume=True)
    snapshots = []

    async def save(state):
        snapshots.append(display.capture(fold))
        return "saved"

    async def model(messages, info):
        yield "finished"

    checkpoint = RootCheckpointCapability(save)
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=[checkpoint]
    )
    async with executable.stream(None, bindings=RunBindings.embedded(), previous_state=previous) as run:
        async for item in run:
            item = await checkpoint.consume(item)
            fold.fold(fold.events(item), source=item)
        result = run.result
    assert result.output_or_raise() == "partial finished"
    assert visible(snapshots[0]) == (
        ["First", "Continue", "partial "] if adjacent_requests else ["Continue", "partial "]
    )
    assert visible(display.capture(fold))[-2:] == ["partial ", "finished"]


def test_initial_import_preserves_part_identity_provenance_and_context() -> None:
    from pydantic_ai.messages import TextContent

    messages = [
        ModelRequest(
            parts=[
                UserPromptPart(
                    [
                        TextContent("First", metadata={"source_id": "input-one"}),
                        TextContent("Second", metadata={"source_id": "input-one"}),
                    ]
                )
            ]
        ),
        ModelRequest(parts=[UserPromptPart("Steer")], metadata={"a13n.steering-run": "run-one"}),
        ModelRequest(
            parts=[UserPromptPart("Hidden")],
            metadata={"a13n.steering-run": "run-one", "a13n.steering-source": "background_process"},
        ),
        ModelRequest(
            parts=[UserPromptPart("# Handoff\n\nKeep this"), UserPromptPart("Internal instructions")],
            metadata={"a13n.context": "handoff"},
        ),
        ModelResponse(
            parts=[TextPart("# Compact\n\nKeep that")], metadata={"keep": "compact", "operation_id": "compact-one"}
        ),
        ModelResponse(parts=[TextPart("Answer")]),
    ]
    saved = import_display_history("thread-one", messages)
    reopened = DisplayHistory.model_validate_json(saved.model_dump_json())
    assert visible(reopened) == ["First", "Second", "Steer", "Answer"]
    (turn,) = display_turns(reopened)
    assert turn.preview == "First Second" and turn.steering_count == 1
    assert turn.output_preview == "Answer" and turn.final_position is None
    summaries = [
        item
        for item in reopened.items
        if item.content.get("name") in {"a13n.context.handoff_summary", "a13n.context.compaction_summary"}
    ]
    assert [item.content["value"]["event"]["summary"] for item in summaries] == [
        "# Handoff\n\nKeep this",
        "# Compact\n\nKeep that",
    ]
    assert "Internal instructions" not in reopened.model_dump_json()


def test_initial_import_keeps_native_tools_retry_and_visible_media_only() -> None:
    from pydantic_ai.messages import (
        ImageUrl,
        NativeToolCallPart,
        NativeToolReturnPart,
        RetryPromptPart,
        ToolCallPart,
        ToolReturnPart,
    )

    saved = import_display_history(
        "thread-one",
        [
            ModelRequest(parts=[UserPromptPart("Search")]),
            ModelResponse(
                parts=[
                    NativeToolCallPart("web_search", {"query": "fact"}, "native-call", provider_name="provider"),
                    ToolCallPart("view", {}, "local-call"),
                ]
            ),
            ModelResponse(
                parts=[NativeToolReturnPart("web_search", {"answer": "found"}, "native-call", provider_name="provider")]
            ),
            ModelRequest(parts=[RetryPromptPart("Try again", tool_name="view", tool_call_id="local-call")]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        "view",
                        ["Visible", ImageUrl("https://example.com/private.png")],
                        "media-call",
                        metadata={"a13n.tool-content": {"result_index": 0, "items": [{"display": False}]}},
                    )
                ]
            ),
        ],
    )
    saved = DisplayHistory.model_validate_json(saved.model_dump_json())
    tools = {item.content["toolCallId"]: item for item in saved.items if item.kind == "tool_call"}
    assert tools["native-call"].content["provider"] == "provider"
    assert tools["native-call"].content["value"] == {"answer": "found"}
    assert tools["local-call"].state == "failed" and tools["local-call"].content["retry"] is True
    assert tools["media-call"].content["value"] == "Visible"
    assert "private.png" not in saved.model_dump_json()


@pytest.mark.parametrize(
    "response",
    [
        ModelResponse(parts=[TextPart("Working"), ToolCallPart("read", {})]),
        ModelResponse(parts=[TextPart("Partial")], state="suspended"),
        ModelResponse(parts=[TextPart("Summary")], metadata={"keep": "compact"}),
    ],
)
def test_import_does_not_promote_intermediate_output_to_closing_preview(response) -> None:
    imported = import_display_history("thread-one", [ModelRequest(parts=[UserPromptPart("Question")]), response])
    assert display_turns(imported)[0].output_position is None
