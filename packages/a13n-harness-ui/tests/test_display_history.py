from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from a13n_harness import HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities import CompactionCapability, CompactionPolicy, HandoffCapability
from a13n_harness.capabilities.context import _COMPACTION_PROMPT
from a13n_harness.model_context import user_prompt_content
from a13n_harness_ui.display_history import (
    DisplayHistory,
    DisplayHistoryCollector,
    saved_display_history,
    with_display_history,
)
from a13n_harness_ui.root_checkpoint import RootCheckpointCapability
from a13n_harness_ui.thread_projection import _message_entry
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


def visible(history: DisplayHistory) -> list[str]:
    return [
        part.text
        for index, message in enumerate(history.messages)
        for part in _message_entry(index, message).parts
        if part.text and part.metadata.display is not False and part.kind != "system"
    ]


@pytest.mark.parametrize("kind", ["handoff", "compaction"])
async def test_display_history_survives_repeated_context_replacement_and_reload(kind: str) -> None:
    previous = HarnessState.new(
        message_history=[
            ModelRequest(parts=[UserPromptPart("Original user request")]),
            ModelResponse(parts=[TextPart("Original answer")], usage=RequestUsage(input_tokens=1000)),
        ]
    )
    display: DisplayHistory | None = None
    for round_number in range(2):
        collector = DisplayHistoryCollector(previous.message_history, display)
        snapshots: list[DisplayHistory] = []
        requests = 0

        async def save(state: HarnessState, *, snapshots=snapshots, collector=collector) -> str:
            snapshots.append(collector.capture(state.message_history))
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

        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(
                collector,
                RootCheckpointCapability(save),
                HandoffCapability(),
                *((CompactionCapability(CompactionPolicy(trigger_tokens=1)),) if kind == "compaction" else ()),
            ),
        )
        async with executable.stream(
            f"Request {round_number}", bindings=RunBindings.embedded(), previous_state=previous
        ) as run:
            async for _ in run:
                pass
            result = run.result
        assert result is not None and result.state is not None
        result.output_or_raise()
        assert snapshots
        display = collector.capture(result.state.message_history)
        text = visible(display)
        assert text.count("Original user request") == 1
        assert text.count("Original answer") == 1
        for number in range(round_number + 1):
            assert text.count(f"Request {number}") == 1
            assert text.count(f"Answer {number}") == 1
        summaries = [message for message in display.messages if (message.metadata or {}).get("operation_id")]
        assert len(summaries) == round_number + 1
        assert len({message.metadata["operation_id"] for message in summaries if message.metadata}) == len(summaries)
        assert not any(_COMPACTION_PROMPT in item for item in text)
        assert not any(
            isinstance(message, ModelResponse)
            and any(isinstance(part, TextPart) and part.content == "Original answer" for part in message.parts)
            for message in result.state.message_history
        )
        # Reopen exactly the serialized display and native state; display never
        # becomes the model's execution history.
        stored = with_display_history(result.state, display)
        assert stored.message_history == result.state.message_history
        previous = HarnessState.model_validate_json(stored.model_dump_json())
        display = saved_display_history(previous)
        assert display is not None


async def test_repeated_identical_messages_keep_distinct_positions_and_detached_snapshots() -> None:
    message = ModelRequest(parts=[UserPromptPart("Same input")])
    collector = DisplayHistoryCollector([])
    snapshot = collector.capture([message, message])
    message.parts = [UserPromptPart("Updated input")]
    assert visible(snapshot) == ["Same input", "Same input"]
    messages = [message, message, ModelResponse(parts=[TextPart("Answer")])]
    updated = collector.capture(messages)
    assert visible(updated) == ["Updated input", "Updated input", "Answer"]
    assert visible(snapshot) == ["Same input", "Same input"]
    native = HarnessState.new(message_history=messages)
    stored = with_display_history(native, updated)
    assert saved_display_history(stored) == updated
    # Old writers can keep unknown state while advancing model history. The
    # stale UI mapping must not corrupt their next transcript or block resume.
    advanced = HarnessState.new(
        message_history=[*native.message_history, ModelRequest(parts=[UserPromptPart("New")])],
        agent_context_state=stored.agent_context_state,
    )
    assert saved_display_history(advanced) is None


@pytest.mark.parametrize("checkpoint", [False, True])
@pytest.mark.parametrize("adjacent_requests", [False, True])
async def test_suspended_response_replaces_its_display_slot_on_resume(
    checkpoint: bool, adjacent_requests: bool
) -> None:
    previous = HarnessState.new(
        message_history=[
            *([ModelRequest(parts=[UserPromptPart("First")], run_id="old")] if adjacent_requests else []),
            ModelRequest(parts=[UserPromptPart("Continue")], run_id="old"),
            ModelResponse(parts=[TextPart("partial ")], state="suspended", run_id="old"),
        ]
    )
    collector = DisplayHistoryCollector(previous.message_history)
    snapshots: list[tuple[HarnessState, DisplayHistory]] = []

    async def save(state: HarnessState) -> str:
        snapshots.append((state, collector.capture(state.message_history)))
        return "saved"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "finished"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=[collector, *([RootCheckpointCapability(save)] if checkpoint else [])],
    )
    result = await executable.run(None, bindings=RunBindings.embedded(), previous_state=previous)
    result.output_or_raise()
    assert result.state is not None
    final = collector.capture(result.state.message_history)
    inputs = ["First", "Continue"] if adjacent_requests else ["Continue"]
    assert visible(final) == [*inputs, "partial ", "finished"]
    assert len(final.messages) == len(inputs) + 1
    assert isinstance(final.messages[-1], ModelResponse)
    assert final.messages[-1].state == "complete"
    assert final.pending_response_position is None
    if checkpoint:
        state, saved = snapshots[0]
        assert visible(saved) == [*inputs, "partial "]
        assert len(saved.model_positions) == len(state.message_history)
        assert saved.pending_response_position == len(inputs)
        # A crash after the provider-boundary checkpoint must not strand the
        # partial response in display history alongside the retried response.
        reopened = DisplayHistoryCollector(
            state.message_history, DisplayHistory.model_validate_json(saved.model_dump_json())
        )
        retry = HarnessBuilder().build(
            AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=[reopened]
        )
        retried = await retry.run(None, bindings=RunBindings.embedded(), previous_state=state)
        retried.output_or_raise()
        assert retried.state is not None
        assert visible(reopened.capture(retried.state.message_history)) == [*inputs, "finished"]


@pytest.mark.parametrize("kind", ["handoff", "compaction"])
async def test_context_summary_projection_keeps_complete_markdown(kind: str) -> None:
    from pydantic_ai.messages import TextContent

    content = "# Decisions\n\n" + "**Keep this complete.**\n\n" * 4000
    metadata = {"a13n.context": kind, "operation_id": "summary-long"}
    message = (
        ModelRequest(parts=[UserPromptPart([TextContent(content, metadata=metadata)])], metadata=metadata)
        if kind == "handoff"
        else ModelResponse(parts=[TextPart(content)], metadata={**metadata, "keep": "compact"})
    )
    entry = _message_entry(0, message)
    assert entry.parts[0].text == content
    assert type(entry).model_validate_json(entry.model_dump_json()) == entry
