from __future__ import annotations

from a13n_stream_protocol.display import DisplayPosition, DisplayScope, DisplaySnapshot, DisplayState, Producer
from a13n_stream_protocol.projector import DisplayProjector
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
    UserPromptPart,
)


def _projector(*, max_blocks: int | None = None):
    baseline = DisplaySnapshot(position=DisplayPosition(producer=Producer(run_id="run", generation="1")))
    receiver = DisplayState(baseline)
    projector = DisplayProjector(baseline, publish=receiver.apply, max_blocks=max_blocks)
    projector.scope(DisplayScope(id="root", thread_id="thread", run_id="run"))
    return projector, receiver


def test_live_parts_and_complete_history_use_the_same_blocks() -> None:
    projector, receiver = _projector()
    projector.reconcile_message("root", 0, ModelRequest(parts=[UserPromptPart("same text")]))
    projector.observe("root", 1, PartStartEvent(index=0, part=TextPart("same ")))
    projector.observe("root", 1, PartDeltaEvent(index=0, delta=TextPartDelta("text")))
    projector.reconcile_message("root", 1, ModelResponse(parts=[TextPart("same text")]))
    checkpoint = projector.capture()
    # These events arrived after the producer already captured their message.
    projector.observe("root", 1, PartDeltaEvent(index=0, delta=TextPartDelta("text")))
    projector.observe("root", 1, PartEndEvent(index=0, part=TextPart("same text")))
    assert projector.capture() == checkpoint
    assert len(checkpoint.blocks) == 2  # Same text is not the same source.
    assert receiver.capture().blocks == checkpoint.blocks


def test_tool_argument_completion_is_not_execution_completion() -> None:
    projector, receiver = _projector()
    projector.observe("root", 0, PartStartEvent(index=0, part=ToolCallPart("search", '{"q":', tool_call_id="call")))
    projector.observe("root", 0, PartDeltaEvent(index=0, delta=ToolCallPartDelta(args_delta='"test"}')))
    projector.observe(
        "root", 0, PartEndEvent(index=0, part=ToolCallPart("search", '{"q":"test"}', tool_call_id="call"))
    )
    block = projector.capture().blocks[0]
    assert block.content["arguments"] == '{"q":"test"}'
    assert block.content["arguments_complete"] is True
    assert block.status == "pending"
    projector.tool_status("root", "call", "running")
    assert projector.capture().blocks[0].status == "running"
    projector.observe("root", 0, FunctionToolResultEvent(ToolReturnPart("search", "found", tool_call_id="call")))
    assert projector.capture().blocks[0].status == "succeeded"
    assert projector.capture().blocks[0].content["result"] == "found"
    assert len(projector.capture().blocks) == 1
    assert receiver.capture().blocks == projector.capture().blocks


def test_retry_failure_is_not_presented_as_a_successful_tool_return() -> None:
    projector, _ = _projector()
    projector.reconcile_message("root", 0, ModelResponse(parts=[ToolCallPart("search", {}, tool_call_id="call")]))
    projector.observe(
        "root",
        0,
        FunctionToolResultEvent(RetryPromptPart("permission denied", tool_name="search", tool_call_id="call")),
    )
    assert projector.capture().blocks[0].status == "failed"
    assert projector.capture().blocks[0].content["retry"] is True


def test_retention_eviction_is_delivered_to_receivers() -> None:
    projector, receiver = _projector(max_blocks=2)
    for index in range(5):
        projector.reconcile_message("root", index, ModelResponse(parts=[TextPart(str(index))]))
    snapshot = projector.capture()
    assert len(snapshot.blocks) == 2
    assert snapshot.omitted == 3
    assert receiver.capture() == snapshot


def test_native_media_provider_tools_and_custom_capability_are_not_lost() -> None:
    from dataclasses import dataclass

    from pydantic_ai.messages import BinaryContent, CapabilityEvent, FilePart, NativeToolCallPart, NativeToolReturnPart

    @dataclass(kw_only=True)
    class Progress(CapabilityEvent, namespace="test.display", name="progress"):
        text: str

    projector, receiver = _projector()
    projector.reconcile_message(
        "root",
        0,
        ModelResponse(
            parts=[
                FilePart(BinaryContent(data=b"private image bytes", media_type="image/png")),
                NativeToolCallPart("web_search", {"query": "test"}, tool_call_id="native-call"),
                NativeToolReturnPart("web_search", "found", tool_call_id="native-call"),
            ]
        ),
    )
    projector.observe("root", 1, Progress(text="Searching"))
    blocks = projector.capture().blocks
    assert [block.kind for block in blocks] == ["media", "tool_chunk", "extension"]
    assert blocks[1].status == "succeeded"
    assert blocks[2].content["name"] == "test.display.progress"
    assert "private image bytes" not in projector.capture().model_dump_json()
    assert receiver.capture() == projector.capture()


def test_partial_tool_arguments_are_verbatim_until_completion() -> None:
    projector, _ = _projector()
    projector.observe("root", 0, PartStartEvent(index=0, part=ToolCallPart("search", '{"q":', tool_call_id="call")))
    assert projector.capture().blocks[0].content["arguments"] == '{"q":'
    projector.observe("root", 0, PartDeltaEvent(index=0, delta=ToolCallPartDelta(args_delta='"value"}')))
    assert projector.capture().blocks[0].content["arguments"] == '{"q":"value"}'
    projector.reconcile_message(
        "root", 0, ModelResponse(parts=[ToolCallPart("search", {"q": "value"}, tool_call_id="call")])
    )
    projector.observe("root", 0, PartEndEvent(index=0, part=ToolCallPart("search", {}, tool_call_id="call")))
    assert not projector._tools


def test_retention_bytes_and_nested_fields_produce_replayable_operations() -> None:
    baseline = DisplaySnapshot(position=DisplayPosition(producer=Producer(run_id="run", generation="1")))
    receiver = DisplayState(baseline)
    projector = DisplayProjector(baseline, publish=receiver.apply, max_bytes=700, max_field_chars=20)
    projector.scope(DisplayScope(id="root", thread_id="thread", run_id="run"))
    for index in range(8):
        projector.observe("root", index, PartStartEvent(index=0, part=TextPart("x" * 19)))
        projector.observe("root", index, PartDeltaEvent(index=0, delta=TextPartDelta("y" * 100)))
    snapshot = projector.capture()
    assert sum(len(block.model_dump_json().encode()) for block in snapshot.blocks) <= 700
    assert snapshot.omitted > 0
    assert all(
        len(str(block.content["text"])) == 20 and block.content["truncated"] is True for block in snapshot.blocks
    )
    assert receiver.capture() == snapshot


def test_eviction_of_unchanged_block_and_oversized_single_block_are_atomic() -> None:
    projector, receiver = _projector(max_blocks=1)
    for index in range(3):
        projector.reconcile_message("root", index, ModelResponse(parts=[TextPart(str(index))]))
    assert len(projector.capture().blocks) == 1
    assert projector.capture().omitted == 2
    assert receiver.capture() == projector.capture()
    baseline = projector.capture()
    small = DisplayProjector(baseline, max_bytes=10)
    small.reconcile_message("root", 3, ModelResponse(parts=[TextPart("larger than the display budget")]))
    assert small.capture().blocks == ()


def test_context_replacement_prunes_unreachable_continuity() -> None:
    from datetime import UTC, datetime, timedelta

    from a13n_stream_protocol.session import DisplaySession

    projector, _ = _projector(max_blocks=2)
    session = DisplaySession(projector, thread_id="thread")
    session._scope = "root"
    for index in range(100):
        session.capture(
            [
                ModelResponse(
                    parts=[TextPart("summary")],
                    metadata={"keep": "compact"},
                    timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
                ),
                ModelRequest(parts=[UserPromptPart(str(index))]),
            ]
        )
    assert len(session.cursor.owners) <= 2
    assert len(session.cursor.digests) == 1
    assert session.cursor.next_index >= 100
    assert len(projector.capture().blocks) == 2
    assert len(projector.capture().model_dump_json()) < 2000


def test_thinking_signature_replaces_instead_of_appending() -> None:
    from pydantic_ai.messages import ThinkingPart, ThinkingPartDelta

    projector, receiver = _projector()
    projector.observe("root", 0, PartStartEvent(index=0, part=ThinkingPart("start", signature="first")))
    projector.observe(
        "root",
        0,
        PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=" continued", signature_delta="second")),
    )
    assert projector.capture().blocks[0].content == {"text": "start continued", "signature": "second"}
    projector.observe("root", 0, PartDeltaEvent(index=0, delta=ThinkingPartDelta(signature_delta="")))
    assert projector.capture().blocks[0].content["signature"] == ""
    assert receiver.capture() == projector.capture()


def test_custom_scalar_extensions_remain_visible_without_raw_input_serialization() -> None:
    from a13n_harness.events import HarnessExtensionEvent

    projector, receiver = _projector()
    projector.observe_extension(
        thread_id="thread", run_id="root", event=HarnessExtensionEvent(kind="tool", payload=["value", 1])
    )
    assert projector.capture().blocks[0].content["value"] == ["value", 1]
    assert receiver.capture() == projector.capture()


def test_execution_summaries_replace_lifecycle_events_without_losing_provenance() -> None:
    from a13n_harness.events import HarnessExtensionEvent

    projector, receiver = _projector()
    for payload in (
        {"type": "context_snapshot", "request_index": 0, "request_tokens": 42, "trigger_tokens": 100},
        {"type": "model_request_started", "request_id": "model-request-1", "request_index": 0, "message_count": 3},
        {"type": "model_request_completed", "request_id": "model-request-1", "request_index": 0},
    ):
        projector.observe_extension(
            thread_id="thread", run_id="root", event=HarnessExtensionEvent(kind="lifecycle", payload=payload)
        )
    (summary,) = projector.capture().blocks
    assert summary.status == "succeeded" and summary.revision == 3
    assert summary.scope_id == "root"
    assert summary.content == {
        "name": "a13n.display.model_request",
        "value": {
            "type": "model_request",
            "request_id": "model-request-1",
            "request_index": 0,
            "request_tokens": 42,
            "trigger_tokens": 100,
            "message_count": 3,
            "status": "succeeded",
        },
    }
    assert receiver.capture() == projector.capture()


def test_summary_files_task_version_and_terminal_lifecycle_are_producer_facts() -> None:
    from a13n_harness.events import HarnessExtensionEvent

    projector, receiver = _projector()
    projector.summary("root", "handoff", "handoff", "Continue here", files=("src/main.py",))
    for event_type in ("handoff_started", "handoff_completed", "handoff_started", "handoff_failed"):
        projector.observe_extension(
            thread_id="thread",
            run_id="root",
            event=HarnessExtensionEvent(kind="context", payload={"type": event_type, "operation_id": "handoff"}),
        )
    projector.observe_extension(
        thread_id="thread",
        run_id="root",
        event=HarnessExtensionEvent(
            kind="state",
            payload={
                "type": "task_changed",
                "task": {"id": "task-1", "status": "completed"},
                "task_state_version": 7,
            },
        ),
    )
    blocks = receiver.blocks
    assert blocks["root:context:handoff"].content["files"] == ["src/main.py"]
    assert blocks["root:execution:handoff"].status == "succeeded"
    assert blocks["root:execution:handoff"].content["value"]["status"] == "succeeded"
    assert blocks["root:task:task-1"].content["task_state_version"] == 7
    assert receiver.capture().blocks == projector.capture().blocks
