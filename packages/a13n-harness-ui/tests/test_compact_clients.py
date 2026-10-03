"""Compact presentation preserves response membership and inline origin without replay."""

import pytest
from a13n_harness_ui.display_history import DisplayHistory, import_display_history
from a13n_harness_ui.display_projection import display_entry, display_turns
from a13n_harness_ui.interactive.history import restore_transcript
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.storage import Thread
from a13n_harness_ui.surfaces import TranscriptPage
from a13n_stream_protocol.display import AppendItem, SetItem
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart


def test_final_response_retains_all_parts_but_not_prior_or_inline_text() -> None:
    original = import_display_history("thread", [ModelRequest(parts=[UserPromptPart("Question")])])
    fold = original.start("run", resume=False)
    renderer = StreamRenderer(Status(mode="detailed"))

    def emit(payload):
        for observation in fold.fold([payload]):
            renderer.ingest(observation.changes)

    def request():
        emit(
            {
                "type": "CUSTOM",
                "name": "a13n.harness.lifecycle",
                "value": {"event": {"payload": {"type": "model_request_started"}}},
            }
        )

    def text(identity, value, child=None):
        scope = {"subagentRunId": child} if child else {}
        emit({"type": "TEXT_MESSAGE_START", "messageId": identity, "role": "assistant", **scope})
        emit({"type": "TEXT_MESSAGE_CONTENT", "messageId": identity, "delta": value, **scope})
        emit({"type": "TEXT_MESSAGE_END", "messageId": identity, **scope})

    request()
    text("progress", "Working")
    request()
    text("first", "First answer part")
    text("first", "Child answer", "child-run")
    text("second", "Second answer part")
    saved = DisplayHistory.model_validate_json(original.capture(fold, completed=True).model_dump_json())
    output = [item for item in saved.items if item.id in saved.completed]
    assert [item.content["text"] for item in output] == ["First answer part", "Second answer part"]
    turn = display_turns(saved)[0]
    assert turn.output_positions == tuple(item.ordinal - 1 for item in output)
    assert turn.final_position == turn.output_position == turn.output_positions[-1]
    assert turn.output_preview == "First answer part Second answer part"
    thread = Thread.model_construct(thread_id="thread", continuation=None)
    entries = tuple(display_entry(item, thread) for item in saved.items)
    child = next(part for entry in entries for part in entry.parts if part.text == "Child answer")
    assert child.subagent_run_id == "child-run" and child.comment_target is None
    restored = StreamRenderer(Status())
    restore_transcript(restored, TranscriptPage(entries=entries, total=len(entries)))
    assert "Subagent · child-run" in restored.drain()
    assert "Child answer" in renderer.drain()


def test_initial_import_keeps_multipart_closing_preview() -> None:
    saved = import_display_history(
        "thread",
        [
            ModelRequest(parts=[UserPromptPart("Question")]),
            ModelResponse(parts=[TextPart("First"), TextPart("Second")]),
        ],
    )
    turn = display_turns(saved)[0]
    assert len(turn.output_positions) == 2
    assert turn.output_preview == "First Second"


def test_native_client_applies_changes_without_raw_events_and_reports_missing_baseline() -> None:
    original = DisplayHistory()
    fold = original.start("run", resume=False)
    renderer = StreamRenderer(Status())
    observed = fold.fold(
        [
            {"type": "TEXT_MESSAGE_START", "messageId": "one", "role": "assistant"},
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "one", "delta": "Hello"},
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "one", "delta": " world"},
        ]
    )
    changes = [change for item in observed for change in item.changes]
    assert isinstance(changes[0], SetItem) and isinstance(changes[-1], AppendItem)
    renderer.ingest(changes)
    assert renderer.drain() == "Hello world"
    renderer.ingest((SetItem(item=fold.items[changes[0].item.id]),))
    assert renderer.drain() == ""
    assert [block.source for block in renderer.transcript.blocks.values()] == ["Hello world"]
    missing = StreamRenderer(Status())
    missing.ingest((changes[-1],))
    assert missing.gap and not missing.transcript.blocks


def test_native_compact_working_set_and_arguments_obey_display_budgets() -> None:
    fold = DisplayHistory().start("run", resume=False)
    renderer = StreamRenderer(Status(), limit=128)
    for payload in [
        {"type": "TOOL_CALL_START", "toolCallId": "call", "toolCallName": "view"},
        *({"type": "TOOL_CALL_ARGS", "toolCallId": "call", "delta": "x" * 1000} for _ in range(100)),
        {"type": "TOOL_CALL_END", "toolCallId": "call"},
    ]:
        for observed in fold.fold([payload]):
            renderer.ingest(observed.changes)
    assert len(next(iter(renderer._items.values())).content["arguments"]) == 128
    assert sum(renderer._item_sizes.values()) <= renderer.transcript.max_bytes
    assert renderer._tools[("root", "call")].truncated


def test_native_hides_metadata_suppressed_tools_and_observations() -> None:
    fold = DisplayHistory().start("run", resume=False)
    renderer = StreamRenderer(Status(mode="detailed"))
    for payload in [
        {"type": "TOOL_CALL_START", "toolCallId": "hidden", "toolCallName": "view", "metadata": {"display": False}},
        {
            "type": "CUSTOM",
            "name": "a13n.shell.status",
            "metadata": {"display": False},
            "value": {"event": {"process_id": "hidden-process", "phase": "exited", "callback": True}},
        },
    ]:
        for observation in fold.fold([payload]):
            renderer.ingest(observation.changes)
    assert not renderer.transcript.blocks
    assert renderer.drain() == ""


@pytest.mark.anyio
async def test_native_multipart_tool_result_completes_through_real_harness_stream() -> None:
    from a13n_harness import HarnessBuilder, RunBindings
    from pydantic_ai.agent.spec import AgentSpec
    from pydantic_ai.capabilities import Capability
    from pydantic_ai.messages import ImageUrl, TextContent
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from pydantic_ai.tools import Tool

    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {0: DeltaToolCall(name="read_image", json_args="{}", tool_call_id="call")}
        else:
            yield "done"

    def read_image():
        return [TextContent("Image inspected"), ImageUrl("https://example.test/image.png")]

    executor = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(Capability(tools=[Tool(read_image)]),),
    )
    fold = DisplayHistory().start("root", resume=False)
    renderer = StreamRenderer(Status(mode="detailed"))
    try:
        async with executor.stream("Inspect", bindings=RunBindings.embedded()) as stream:
            async for item in stream:
                for observation in fold.fold(fold.events(item), source=item):
                    renderer.ingest(observation.changes)
        tool = next(item for item in fold.items.values() if item.kind == "tool_call")
        assert tool.state == "completed" and "result_parts" in tool.content
        text = "\n".join(block.source for block in renderer.transcript.blocks.values())
        assert "Image inspected" in text and "https://example.test/image.png" in text
        assert "running" not in text and not renderer._tools
    finally:
        renderer.transcript.close()


def test_inline_child_completion_only_ends_its_process_observations() -> None:
    import json

    fold = DisplayHistory().start("root", resume=False)
    renderer = StreamRenderer(Status(mode="detailed"))

    def emit(payload):
        for observation in fold.fold([payload]):
            renderer.ingest(observation.changes, run_id="root")

    try:
        for scope in ({}, {"subagentRunId": "child"}):
            emit({"type": "TOOL_CALL_START", "toolCallId": "call", "toolCallName": "shell_exec", **scope})
            emit({"type": "TOOL_CALL_ARGS", "toolCallId": "call", "delta": '{"command":"check"}', **scope})
            emit({"type": "TOOL_CALL_END", "toolCallId": "call", **scope})
            emit(
                {
                    "type": "TOOL_CALL_RESULT",
                    "toolCallId": "call",
                    "content": json.dumps({"ok": True, "process_id": "process", "status": {"phase": "running"}}),
                    **scope,
                }
            )
        assert renderer._shell_processes[("root", "process")].phase == "running"
        assert renderer._shell_processes[("child", "process")].phase == "running"
        emit({"type": "SUBAGENT_FINISHED", "subagentRunId": "child"})
        assert renderer._shell_processes[("root", "process")].phase == "running"
        assert renderer._shell_processes[("child", "process")].phase == "unavailable"
    finally:
        renderer.transcript.close()
