"""Shared compact display continuity and Harness UI presentation."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from a13n_harness import HarnessBuilder, HarnessState, RunBindings
from a13n_harness_ui.display import baseline
from a13n_harness_ui.display_projection import transcript
from a13n_harness_ui.root_checkpoint import RootCheckpointCapability
from a13n_stream_protocol.display import DisplayScope, DisplaySnapshot
from a13n_stream_protocol.projector import DisplayProjector
from a13n_stream_protocol.session import DisplayCapture
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


async def test_repeated_identical_messages_keep_distinct_addresses_and_detached_snapshots() -> None:
    state = HarnessState.new()
    saved = None
    captures = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "same answer"

    for index in range(3):
        projector = DisplayProjector(baseline(f"run-{index}", saved))
        capture = DisplayCapture(projector)

        async def save(state: HarnessState, snapshot: DisplaySnapshot | None) -> str:
            assert snapshot is not None
            captures.append(snapshot)
            return "checkpoint"

        executable = HarnessBuilder().build(
            AgentSpec(),
            model=FunctionModel(stream_function=model),
            output_type=str,
            capabilities=(capture, RootCheckpointCapability(save, display=capture)),
        )
        result = await executable.run("same input", previous_state=state, bindings=RunBindings.embedded())
        assert result.output_or_raise() == "same answer" and result.state is not None
        saved = capture.capture(result.run_id, result.state.message_history)
        saved = DisplaySnapshot.model_validate_json(saved.model_dump_json())
        state = HarnessState.model_validate_json(result.state.model_dump_json())
        assert "a13n.harness-ui.display-history" not in state.agent_context_state.entries
    assert saved is not None
    assert [block.content["text"] for block in saved.blocks if block.kind == "input"] == ["same input"] * 3
    answers = [block for block in saved.blocks if block.kind == "text"]
    assert len({(block.message_index, block.part_index) for block in answers}) == 3
    assert not any(block.kind == "text" for block in captures[0].blocks)
    entries, turns = transcript(saved, thread_id=state.thread_id, source_id="a" * 64)
    assert len(turns) == 3
    assert all(turn.final_position is not None for turn in turns)
    assert len([part.comment_target for entry in entries for part in entry.parts if part.comment_target]) == 3


@pytest.mark.parametrize("count", [2, 3])
async def test_preparation_failure_preserves_saved_display_after_native_request_merging(count: int) -> None:
    state = HarnessState.new(
        message_history=[
            *[ModelRequest(parts=[UserPromptPart(f"Old input {index}")]) for index in range(count)],
            ModelResponse(parts=[TextPart("Saved answer")]),
        ]
    )

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "Saved again"

    projector = DisplayProjector(baseline("first"))
    capture = DisplayCapture(projector)
    first = (
        await HarnessBuilder()
        .build(AgentSpec(), model=FunctionModel(stream_function=model), output_type=str, capabilities=(capture,))
        .run("First input", previous_state=state, bindings=RunBindings.embedded())
    )
    assert first.state is not None
    saved = capture.capture(first.run_id, first.state.message_history)
    old_text = [block.content.get("text") for block in saved.blocks]
    projector = DisplayProjector(baseline("second", saved))
    capture = DisplayCapture(projector)

    class FailInstructions(AbstractCapability):
        def get_instructions(self):
            async def instructions(ctx):
                raise RuntimeError("instruction backend unavailable")

            return instructions

    failed = (
        await HarnessBuilder()
        .build(
            AgentSpec(),
            model=FunctionModel(stream_function=model),
            output_type=str,
            capabilities=(capture, FailInstructions()),
        )
        .run("New input", previous_state=first.state, bindings=RunBindings.embedded())
    )
    assert failed.status == "failed" and failed.state is not None
    updated = capture.capture(failed.run_id, failed.state.message_history)
    assert [block.content.get("text") for block in updated.blocks] == [*old_text, "New input"]
    assert saved.blocks == updated.blocks[: len(saved.blocks)]


@pytest.mark.parametrize("kind", ["handoff", "compaction"])
async def test_context_summary_projection_keeps_complete_markdown(kind: str) -> None:
    content = "# Decisions\n\n" + "**Keep this complete.**\n\n" * 4000
    projector = DisplayProjector(baseline("run"))
    projector.scope(DisplayScope(id="run", run_id="run", thread_id="thread"))
    projector.summary("run", "summary-long", kind, content)
    entries, _ = transcript(projector.capture(), thread_id="thread", source_id=None)
    assert entries[0].parts[0].text == content
    assert entries[0].parts[0].metadata.model_dump()["operation_id"] == "summary-long"
    assert type(entries[0]).model_validate_json(entries[0].model_dump_json()) == entries[0]


def test_shared_tool_media_lineage_provider_and_outcome_survive_saved_presentation() -> None:
    from a13n_harness_ui.display_projection import child_presentation
    from pydantic_ai.messages import (
        FunctionToolResultEvent,
        ImageUrl,
        NativeToolCallPart,
        NativeToolReturnPart,
        RetryPromptPart,
        TextContent,
        ToolReturnPart,
    )

    projector = DisplayProjector(baseline("root"))
    projector.scope(DisplayScope(id="root", run_id="root", thread_id="thread"))
    projector.scope(DisplayScope(id="inline", run_id="inline", thread_id="child", parent_scope_id="root"))
    projector.reconcile_message(
        "root",
        0,
        ModelResponse(
            parts=[
                NativeToolCallPart("search", {}, tool_call_id="native", provider_name="web"),
                NativeToolReturnPart("search", "found", tool_call_id="native", provider_name="web"),
            ]
        ),
    )
    projector.observe(
        "inline",
        1,
        FunctionToolResultEvent(
            ToolReturnPart(
                "observe",
                [TextContent("same"), ImageUrl("https://example.test/image.png"), TextContent("same")],
                tool_call_id="media",
                outcome="denied",
            )
        ),
    )
    projector.observe(
        "root",
        2,
        FunctionToolResultEvent(
            RetryPromptPart(
                "invalid argument",
                tool_name="search",
                tool_call_id="retry",
            )
        ),
    )
    snapshot = DisplaySnapshot.model_validate_json(projector.capture().model_dump_json())
    entries, _ = transcript(snapshot, thread_id="thread", source_id=None)
    parts = [part for entry in entries for part in entry.parts]
    native = [part for part in parts if part.tool_call_id == "native"]
    assert [part.provider for part in native] == ["web", "web"]
    media = next(part for part in parts if part.tool_call_id == "media" and part.kind == "tool_result")
    assert media.outcome == "denied" and media.value is None
    assert [item["type"] for item in media.content_parts] == ["text", "image", "text"]
    assert media.content_parts[0] == media.content_parts[2]
    assert any(
        part.kind == "retry"
        and part.text == RetryPromptPart("invalid argument", tool_name="search", tool_call_id="retry").model_response()
        for part in parts
    )
    child = child_presentation(snapshot)
    activity = next(item for item in child.activities if item.tool_name == "observe")
    assert activity.subagent_run_id == "inline"
    assert activity.content_parts == media.content_parts
    assert next(item for item in child.activities if item.tool_name == "search").subagent_run_id is None
