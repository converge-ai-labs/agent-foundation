from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence

import pytest
from a13n_harness import HarnessBuilder, HarnessEvent, HarnessRunStream, HarnessState, ModelRecoveryPolicy, RunBindings
from a13n_harness.capabilities import (
    CompactionCapability,
    CompactionPolicy,
    HandoffCapability,
    RuntimeContextCapability,
)
from a13n_harness.capabilities.context import _COMPACTION_PROMPT
from a13n_harness.events import InputTextEvent
from a13n_harness.model_context import user_prompt_content
from a13n_harness_ui.conversation import ConversationExcerpt, checkpoint_excerpt
from a13n_harness_ui.root_checkpoint import RootCheckpointCapability, ThreadCheckpointEvent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import (
    EnqueuedMessagesEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextContent,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


def _user_text(messages: Sequence[ModelMessage]) -> str:
    return "\n".join(
        item.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
        for item in user_prompt_content(part)
        if isinstance(item, TextContent)
    )


async def _consume(run: HarnessRunStream[str], events: list[HarnessEvent]) -> None:
    async for item in run:
        if isinstance(item, HarnessEvent):
            events.append(item)


async def test_checkpoint_exports_committed_handoff_and_context_not_provider_merged_history() -> None:
    saved: list[HarnessState] = []
    calls: list[list[ModelMessage]] = []
    events: list[HarnessEvent] = []

    async def save(state: HarnessState) -> str:
        saved.append(state)
        return f"checkpoint-{len(saved)}"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        calls.append(messages)
        # The actual Pydantic wrapper must save before dispatch, not after output.
        assert len(saved) == len(calls)
        if len(calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps({"content": "Continue with the completed design."}),
                    tool_call_id="handoff-checkpoint",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(instructions="Keep the standing instruction."),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(RootCheckpointCapability(save), RuntimeContextCapability(), HandoffCapability()),
    )
    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("Obsolete background")]),
            ModelResponse(parts=[TextPart("Obsolete answer")]),
        )
    )
    async with executable.stream(
        "Build the feature",
        bindings=RunBindings.embedded(),
        previous_state=previous,
    ) as run:
        await _consume(run, events)
        result = run.result

    assert result is not None
    assert result.output_or_raise() == "done"
    assert len(saved) == 2
    excerpt = checkpoint_excerpt(ConversationExcerpt(), saved[-1].message_history)
    assert excerpt.first_input == excerpt.latest_input == "Build the feature"
    checkpoint = saved[-1]
    assert checkpoint.agent_context_state.entries["a13n.handoff"].data["summary"] is None
    text = _user_text(checkpoint.message_history)
    assert "Continue with the completed design." in text
    assert "Build the feature" in text
    assert "Obsolete background" not in text
    assert '<runtime-context source="a13n-harness">' in text
    assert not any(isinstance(part, ToolCallPart) for message in checkpoint.message_history for part in message.parts)
    # Native preparation merges requests only for the provider. The checkpoint
    # must retain the separate canonical messages after the before-hook rewrite.
    assert len(calls[-1]) == 1
    assert len(checkpoint.message_history) > len(calls[-1])
    assert result.state is not None
    assert checkpoint.message_history == result.state.message_history[:-1]
    markers = [event.event.continuation_id for event in events if isinstance(event.event, ThreadCheckpointEvent)]
    assert markers == ["checkpoint-1", "checkpoint-2"]
    first_input = next(
        i for i, event in enumerate(events) if isinstance(event.event, InputTextEvent) and event.event.source == "user"
    )
    first_marker = next(i for i, event in enumerate(events) if isinstance(event.event, ThreadCheckpointEvent))
    assert first_input < first_marker


@pytest.mark.parametrize("cancel_helper", [False, True])
async def test_checkpoint_excludes_nested_compaction_and_rebinds_after_outer_exit(cancel_helper: bool) -> None:
    saved: list[HarnessState] = []
    events: list[HarnessEvent] = []
    helper_started = asyncio.Event()
    release_helper = asyncio.Event()
    provider_calls: list[str] = []

    async def save(state: HarnessState) -> str:
        saved.append(state)
        return f"checkpoint-{len(saved)}"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        if _COMPACTION_PROMPT in _user_text(messages):
            provider_calls.append("helper")
            helper_started.set()
            await release_helper.wait()
            yield "Compacted continuation"
        else:
            provider_calls.append("root")
            assert saved
            yield "done"

    checkpoint = RootCheckpointCapability(save)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(checkpoint, CompactionCapability(CompactionPolicy(trigger_tokens=2_000))),
    )
    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("Long original task")]),
            ModelResponse(parts=[TextPart("Previous answer")], usage=RequestUsage(input_tokens=2_100)),
        )
    )
    async with executable.stream(
        "Continue",
        bindings=RunBindings.embedded(),
        previous_state=previous,
    ) as run:
        consumer = asyncio.create_task(_consume(run, events))
        try:
            await asyncio.wait_for(helper_started.wait(), timeout=5)
            assert saved == []
            assert not any(isinstance(event.event, ThreadCheckpointEvent) for event in events)
            if cancel_helper:
                run.cancel()
            else:
                release_helper.set()
            await asyncio.wait_for(consumer, timeout=5)
        finally:
            release_helper.set()
            if not consumer.done():
                run.cancel()
                await asyncio.wait_for(consumer, timeout=5)
        first = run.result

    assert first is not None
    assert first.status == ("cancelled" if cancel_helper else "completed")
    assert provider_calls == (["helper"] if cancel_helper else ["helper", "root"])
    assert len(saved) == (0 if cancel_helper else 1)
    if not cancel_helper:
        assert first.state is not None
        assert saved[0].message_history == first.state.message_history[:-1]
        assert "Compacted continuation" in str(saved[0].message_history)
        # Older continuations may have no display excerpts. Synthetic summary
        # instructions must not become the first authored input after compaction.
        excerpt = checkpoint_excerpt(ConversationExcerpt(), saved[0].message_history)
        assert excerpt.first_input == excerpt.latest_input == "Continue"
    assert all(_COMPACTION_PROMPT not in _user_text(state.message_history) for state in saved)

    # Reusing this same run-bound object must bind the next outer native run,
    # even when the previous outer run exited by cancellation inside its helper.
    before = len(saved)
    second = await executable.run("New outer request", bindings=RunBindings.embedded())
    assert second.output_or_raise() == "done"
    assert len(saved) == before + 1
    assert "New outer request" in _user_text(saved[-1].message_history)


async def test_failed_checkpoint_emits_no_marker_or_model_request_and_can_be_reused() -> None:
    events: list[HarnessEvent] = []
    attempts: list[HarnessState] = []
    calls = 0

    async def save(state: HarnessState) -> str:
        attempts.append(state)
        if len(attempts) == 1:
            raise OSError("checkpoint disk unavailable")
        return "selected-after-failure"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        yield "done"

    checkpoint = RootCheckpointCapability(save)
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=(checkpoint,)
    )
    async with executable.stream("Unsaved input", bindings=RunBindings.embedded()) as run:
        await _consume(run, events)
        failed = run.result

    assert failed is not None
    assert failed.status == "failed"
    assert len(attempts) == 1
    assert calls == 0
    assert not any(isinstance(event.event, ThreadCheckpointEvent) for event in events)

    events.clear()
    async with executable.stream("Retry input", bindings=RunBindings.embedded()) as run:
        await _consume(run, events)
        recovered = run.result
    assert recovered is not None
    assert recovered.output_or_raise() == "done"
    assert calls == 1
    assert len(attempts) == 2
    assert [event.event.continuation_id for event in events if isinstance(event.event, ThreadCheckpointEvent)] == [
        "selected-after-failure"
    ]


@pytest.mark.parametrize("compaction_enabled", [False, True])
async def test_consumed_steering_is_saved_once_before_its_native_checkpoint_marker(compaction_enabled: bool) -> None:
    saved: list[HarnessState] = []
    events: list[HarnessEvent] = []
    captured: list[HarnessEvent] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def save(state: HarnessState) -> str:
        if saved:
            assert any(
                isinstance(item.event, InputTextEvent) and item.event.content == "Focus on correctness"
                for item in captured
            )
        saved.append(state)
        return f"checkpoint-{len(saved)}"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        assert len(saved) == calls
        if calls == 1:
            first_started.set()
            await release_first.wait()
            yield "first answer"
        else:
            yield "steered answer"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            RootCheckpointCapability(save),
            HandoffCapability(),
            *((CompactionCapability(CompactionPolicy(trigger_tokens=1_000_000)),) if compaction_enabled else ()),
        ),
    )
    async with executable.stream(
        "Initial task", bindings=RunBindings.embedded(producer_observer=captured.append)
    ) as run:
        consumer = asyncio.create_task(_consume(run, events))
        try:
            await asyncio.wait_for(first_started.wait(), timeout=5)
            enqueue_id = await run.steer("Focus on correctness")
            assert enqueue_id
            # Accepted but not consumed steering is deliberately outside this
            # boundary: it must not appear in the already selected checkpoint.
            assert len(saved) == 1
            assert "Focus on correctness" not in _user_text(saved[0].message_history)
            release_first.set()
            await asyncio.wait_for(consumer, timeout=5)
        finally:
            release_first.set()
            if not consumer.done():
                run.cancel()
                await asyncio.wait_for(consumer, timeout=5)
        result = run.result

    assert result is not None
    assert result.output_or_raise() == "steered answer"
    assert calls == len(saved) == 2
    assert _user_text(saved[-1].message_history).count("Focus on correctness") == 1
    retained = saved[-1].agent_context_state.entries["a13n.steering"].data["retained_requests"]
    assert len(retained) == 2
    assert result.state is not None
    assert saved[-1].message_history == result.state.message_history[:-1]
    ordered = [
        event.event for event in events if isinstance(event.event, (ThreadCheckpointEvent, EnqueuedMessagesEvent))
    ]
    assert len(ordered) == 3
    assert isinstance(ordered[0], ThreadCheckpointEvent)
    assert ordered[0].continuation_id == "checkpoint-1"
    assert isinstance(ordered[1], EnqueuedMessagesEvent)
    assert isinstance(ordered[2], ThreadCheckpointEvent)
    assert ordered[2].continuation_id == "checkpoint-2"


async def test_checkpoint_rebinds_to_native_recovery_attempt_in_the_same_harness_run() -> None:
    saved: list[HarnessState] = []
    events: list[HarnessEvent] = []
    calls = 0

    async def save(state: HarnessState) -> str:
        saved.append(state)
        return f"checkpoint-{len(saved)}"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        assert len(saved) == calls
        if calls == 1:
            raise ConnectionResetError("stream disconnected before the first response")
        yield "recovered"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(RootCheckpointCapability(save),),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
        ),
    )
    async with executable.stream("Recover this request", bindings=RunBindings.embedded()) as run:
        await _consume(run, events)
        result = run.result

    assert result is not None
    assert result.output_or_raise() == "recovered"
    assert calls == len(saved) == 2
    assert saved[0].thread_id == saved[1].thread_id
    assert all(_user_text(state.message_history).count("Recover this request") == 1 for state in saved)
    markers = [event for event in events if isinstance(event.event, ThreadCheckpointEvent)]
    assert len({event.run_id for event in markers}) == 1
    assert [event.event.continuation_id for event in markers] == ["checkpoint-1", "checkpoint-2"]


@pytest.mark.parametrize("compact", [False, True])
async def test_producer_capture_precedes_checkpoint_with_paused_public_consumer(compact: bool) -> None:
    from a13n_harness.capabilities.context import CompactionSummaryEvent
    from a13n_harness.events import InputMediaEvent
    from pydantic_ai.messages import ImageUrl, PartStartEvent

    captured: list[HarnessEvent] = []
    saved: list[HarnessState] = []
    reached = asyncio.Event()

    async def save(state: HarnessState) -> str:
        assert any(
            isinstance(item.event, InputTextEvent) and item.event.content == "Visible input" for item in captured
        )
        assert any(isinstance(item.event, InputMediaEvent) for item in captured)
        if compact:
            assert any(isinstance(item.event, CompactionSummaryEvent) for item in captured)
            assert not any(
                isinstance(item.event, PartStartEvent)
                and isinstance(item.event.part, TextPart)
                and "Helper summary" in item.event.part.content
                for item in captured
            )
        saved.append(state)
        reached.set()
        return "checkpoint"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        if _COMPACTION_PROMPT in _user_text(messages):
            yield "Helper summary"
        else:
            assert saved, "Provider dispatch must wait for publication"
            yield "done"

    capabilities = [RootCheckpointCapability(save)]
    previous = None
    if compact:
        capabilities.append(CompactionCapability(CompactionPolicy(trigger_tokens=2000)))
        previous = HarnessState.new(
            message_history=(
                ModelRequest(parts=[UserPromptPart("Original task")]),
                ModelResponse(parts=[TextPart("Previous answer")], usage=RequestUsage(input_tokens=2100)),
            )
        )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=capabilities,
    )
    async with executable.stream(
        ["Visible input", ImageUrl("https://example.com/image.png")],
        previous_state=previous,
        bindings=RunBindings.embedded(producer_observer=captured.append),
    ) as run:
        await anext(run)
        await asyncio.wait_for(reached.wait(), timeout=5)
        # Only RUN_STARTED has been pulled; capture and save do not depend on delivery.
        assert len(saved) == 1
        async for _ in run:
            pass
        assert run.result is not None and run.result.output_or_raise() == "done"
    assert [item.sequence for item in captured] == list(range(1, len(captured) + 1))


async def test_tool_presentation_retention_finishes_before_next_checkpoint() -> None:
    from a13n_harness_ui.display_history import DisplayHistoryCollector
    from a13n_harness_ui.root_execution import _ToolPresentationCapability
    from ag_ui.core import CustomEvent
    from pydantic_ai.capabilities import Capability
    from pydantic_ai.messages import FunctionToolResultEvent
    from pydantic_ai.tools import Tool

    collector = DisplayHistoryCollector(())
    retaining = asyncio.Event()
    release = asyncio.Event()
    saved = []
    calls = 0

    async def retain(event):
        if not isinstance(event, FunctionToolResultEvent):
            return
        retaining.set()
        await release.wait()
        collector.supplement(
            [
                CustomEvent(
                    name="a13n.harness-ui.tool_images",
                    value={
                        "event": {
                            "tool_call_id": "call",
                            "images": [{"path": "retained.png"}],
                        }
                    },
                ),
                CustomEvent(
                    name="a13n.harness-ui.mcp_apps",
                    value={
                        "event": {
                            "tool_call_id": "call",
                            "apps": [{"snapshot_id": "retained-app"}],
                        }
                    },
                ),
            ]
        )

    async def save(state):
        saved.append(collector.capture(state.message_history))
        return f"checkpoint-{len(saved)}"

    async def screenshot() -> str:
        return "image and app"

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {0: DeltaToolCall(name="screenshot", json_args="{}", tool_call_id="call")}
        else:
            item = next(item for item in saved[-1].items if item.kind == "tool_call")
            assert item.content["tool_images"] == [{"path": "retained.png"}]
            assert item.content["mcp_apps"] == [{"snapshot_id": "retained-app"}]
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            RootCheckpointCapability(save),
            _ToolPresentationCapability(retain),
            Capability(tools=[Tool(screenshot)]),
        ),
    )
    async with executable.stream("Capture", bindings=RunBindings.embedded(producer_observer=collector.observe)) as run:
        consumer = asyncio.create_task(_consume(run, []))
        await asyncio.wait_for(retaining.wait(), timeout=5)
        assert calls == len(saved) == 1
        release.set()
        await consumer
        assert run.result is not None and run.result.output_or_raise() == "done"
    assert len(saved) == 2
