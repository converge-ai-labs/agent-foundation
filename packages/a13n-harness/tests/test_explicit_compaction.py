from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessState
from a13n_harness import AgentSpec as HarnessAgentSpec
from a13n_harness._context_history import _COMPACTION_PROMPT
from a13n_harness.capabilities import (
    CompactionCapability,
    CompactionPolicy,
    HandoffCapability,
    RuntimeContextCapability,
    WorkingStateCapability,
)
from a13n_harness.filters.cold_start import ColdStartFilterCapability
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage, UsageLimits

pytestmark = pytest.mark.anyio


def _previous():
    return HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("Remember the unresolved migration task.")]),
            ModelResponse(parts=[TextPart("We will inspect the migration next.")]),
        )
    )


@pytest.mark.parametrize("instruction", [None, "Preserve the migration plan and unresolved questions."])
async def test_compact_returns_resumable_state_without_continuing(instruction):
    calls = []

    async def stream(messages, info):
        calls.append(deepcopy(messages))
        yield "Condensed conversation summary: migration plan."

    agent = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream))
    previous = _previous()
    frozen = previous.model_dump_json()
    result = await agent.compact(
        previous_state=previous, instruction=instruction, usage_limits=UsageLimits(request_limit=1)
    )
    assert result.status == "completed", result.failure
    assert result.output_or_raise() == "Condensed conversation summary: migration plan."
    assert result.state.thread_id == previous.thread_id
    assert len(calls) == 1
    assert calls[0][: len(previous.message_history)] == list(previous.message_history)
    tail = "\n".join(
        part.content
        for part in calls[0][-1].parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )
    assert "Do not call tools" in tail
    if instruction is None:
        assert _COMPACTION_PROMPT in tail
    else:
        assert instruction in tail
        assert _COMPACTION_PROMPT not in tail
    assert previous.model_dump_json() == frozen
    assert result.state.agent_context_state.entries["a13n.compaction"].data["restoration_pending"] is False
    assert _COMPACTION_PROMPT not in str(result.state.message_history)
    resumed = await agent.run("Continue", previous_state=result.state)
    assert resumed.status == "completed"
    assert len(calls) == 2
    assert "migration plan" in str(calls[1])


async def test_compact_preserves_provider_cache_prefixes_and_cold_tool_returns():
    calls = []

    class RecordingModel(FunctionModel):
        @asynccontextmanager
        async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
            calls.append((deepcopy(messages), deepcopy(model_settings), deepcopy(model_request_parameters)))
            async with super().request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as response:
                yield response

    async def stream(messages, info):
        if len(calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="note_write", json_args='{"key":"plan","value":"Keep this plan"}', tool_call_id="note"
                )
            }
        else:
            yield "History summary."

    agent = HarnessBuilder().build(
        HarnessAgentSpec(system_prompt="Stable system", instructions="Stable instruction"),
        output_type=str,
        model=RecordingModel(stream_function=stream),
        capabilities=(
            RuntimeContextCapability(),
            WorkingStateCapability(),
            ColdStartFilterCapability(),
            CompactionCapability(CompactionPolicy(trigger_tokens=1000000)),
        ),
    )
    first = await agent.run("Plan migration")
    assert first.status == "completed"
    before = len(calls)
    result = await agent.compact(previous_state=first.state, instruction="Keep unresolved tasks")
    assert result.status == "completed", result.failure
    assert len(calls) == before + 1
    chat = OpenAIChatModel("gpt-4.1", provider=OpenAIProvider(api_key="test-only"))
    responses = OpenAIResponsesModel("gpt-4.1", provider=OpenAIProvider(api_key="test-only"))
    claude = AnthropicModel("claude-sonnet-4-6", provider=AnthropicProvider(api_key="test-only"))
    projections = []
    for messages, settings, parameters in (calls[-2], calls[-1]):
        settings = settings or {}
        chat_messages = await chat._map_messages(messages, parameters, model_settings=settings)
        instructions, response_messages = await responses._map_messages(messages, settings, parameters)
        system, claude_messages = await claude._map_message(messages, parameters, settings)
        tools = claude._prepare_tools_and_tool_choice(settings, parameters)
        projections.append((chat_messages, response_messages, claude_messages))
        current = (instructions, system, tools, parameters.function_tools, settings)
        if len(projections) == 1:
            stable = current
        else:
            assert current == stable
    for prefix, extended in zip(*projections, strict=True):
        assert extended[: len(prefix)] == prefix

    old = _previous()
    old = HarnessState.new(
        message_history=(
            old.message_history[0],
            ModelResponse(parts=[ToolCallPart("lookup", {}, tool_call_id="old")]),
            ModelRequest(parts=[ToolReturnPart("lookup", "x" * 5000, tool_call_id="old")]),
            ModelResponse(parts=[TextPart("Read")], timestamp=datetime.now(UTC) - timedelta(days=2)),
        )
    )
    result = await agent.compact(previous_state=old)
    assert result.status == "completed", result.failure
    assert "x" * 5000 in str(calls[-1][0])


@pytest.mark.parametrize("kind", ["tool", "empty", "error"])
async def test_compact_failure_preserves_history_and_never_dispatches_tools(kind):
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if kind == "tool":
            yield {0: DeltaToolCall(name="note_write", json_args='{"key":"bad","value":"bad"}', tool_call_id="bad")}
        elif kind == "empty":
            yield " "
        else:
            raise RuntimeError("provider failed")

    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(WorkingStateCapability(),),
    )
    previous = _previous()
    result = await agent.compact(previous_state=previous)
    assert result.status == "failed"
    assert result.state.message_history == previous.message_history
    assert calls == 1
    assert "a13n.working-state" not in result.state.agent_context_state.entries


@pytest.mark.parametrize(
    "history",
    [
        (),
        (
            ModelRequest(parts=[UserPromptPart("lookup")]),
            ModelResponse(parts=[ToolCallPart("lookup", {}, tool_call_id="pending")]),
        ),
    ],
)
async def test_compact_rejects_ineligible_history_without_model_call(history):
    async def stream(messages, info):
        pytest.fail("model must not run")
        yield "unreachable"

    agent = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream))
    result = await agent.compact(previous_state=HarnessState.new(message_history=history))
    assert result.status == "failed"


async def test_compact_rejects_pending_handoff_without_overwriting_it():
    calls = []

    async def stream(messages, info):
        calls.append(deepcopy(messages))
        if len(calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="summarize", json_args='{"content":"Pending handoff summary"}', tool_call_id="handoff"
                )
            }
        else:
            yield "Continued after handoff"

    agent = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream), capabilities=(HandoffCapability(),)
    )
    pending = await agent.run("Summarize", usage_limits=UsageLimits(request_limit=1))
    assert pending.status == "failed"
    handoff = pending.state.agent_context_state.entries["a13n.handoff"]
    assert handoff.data["summary"] == "# Context Summary\n\nPending handoff summary"
    compacted = await agent.compact(previous_state=pending.state)
    assert compacted.status == "failed"
    assert compacted.failure.code == "compaction_boundary_pending"
    assert len(calls) == 1
    assert compacted.state.message_history == pending.state.message_history
    assert compacted.state.agent_context_state.entries["a13n.handoff"] == handoff
    resumed = await agent.run("Continue", previous_state=compacted.state)
    assert resumed.status == "completed", resumed.failure
    assert "Pending handoff summary" in str(calls[-1])


async def test_compact_obeys_request_budget():
    async def stream(messages, info):
        pytest.fail("model must not run")
        yield "unreachable"

    agent = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream))
    previous = _previous()
    result = await agent.compact(
        previous_state=previous, usage=RunUsage(requests=1), usage_limits=UsageLimits(request_limit=1)
    )
    assert result.status == "failed"
    assert result.state.message_history == previous.message_history


async def test_compact_cancellation_preserves_input_state():
    started = asyncio.Event()

    async def stream(messages, info):
        started.set()
        await asyncio.Event().wait()
        yield "unreachable"

    agent = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream))
    previous = _previous()
    frozen = previous.model_dump_json()
    task = asyncio.create_task(agent.compact(previous_state=previous))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert previous.model_dump_json() == frozen


async def test_compact_can_compact_a_restored_checkpoint_again():
    async def stream(messages, info):
        yield "summary"

    agent = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream))
    first = await agent.compact(previous_state=_previous())
    second = await agent.compact(previous_state=first.state)
    assert second.status == "completed", second.failure


@pytest.mark.parametrize("cancel", [False, True])
async def test_compact_restoration_failure_can_resume(cancel):
    from a13n_harness.capabilities.context import ContextRestoredEvent
    from pydantic_ai.capabilities import AbstractCapability

    contexts = []

    class Listener(AbstractCapability):
        async def on_event(self, ctx, *, event):
            if isinstance(event, ContextRestoredEvent):
                contexts.append(ctx)
                if len(contexts) == 1:
                    raise asyncio.CancelledError() if cancel else RuntimeError("listener failed")

    async def stream(messages, info):
        yield "summary"

    agent = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream), capabilities=(Listener(),)
    )
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await agent.compact(previous_state=_previous())
        state = await contexts[0].deps.export_state(contexts[0].messages)
    else:
        failed = await agent.compact(previous_state=_previous())
        assert failed.status == "failed"
        state = failed.state
    assert state.agent_context_state.entries["a13n.compaction"].data["restoration_pending"] is True
    resumed = await agent.run("continue", previous_state=state)
    assert resumed.status == "completed", resumed.failure
    assert len(contexts) == 2
    assert resumed.state.agent_context_state.entries["a13n.compaction"].data["restoration_pending"] is False


async def test_compact_rejects_structured_only_output_without_changing_tools():
    from pydantic import BaseModel
    from pydantic_ai import NativeOutput

    class Answer(BaseModel):
        value: str

    async def stream(messages, info):
        pytest.fail("unsupported output mode must fail before dispatch")
        yield "unreachable"

    agent = HarnessBuilder().build(
        AgentSpec(), output_type=NativeOutput(Answer), model=FunctionModel(stream_function=stream)
    )
    previous = _previous()
    result = await agent.compact(previous_state=previous)
    assert result.status == "failed"
    assert result.failure.code == "compaction_text_output_required"
    assert result.state.message_history == previous.message_history


async def test_compact_preserves_stored_state_and_plugin_lifecycle():
    from a13n_harness import RunBindings
    from a13n_harness.state import AgentContextStateSnapshot, CapabilityState
    from a13n_harness.storage import RunStorage

    from .test_plugins import RecordingPlugin
    from .test_state_store import MemoryStore

    store = MemoryStore()
    content = "History to preserve. " * 10000
    opaque = CapabilityState(version="future", data={"keep": "value"})
    previous = HarnessState.new(
        message_history=[ModelRequest(parts=[UserPromptPart(content)]), ModelResponse(parts=[TextPart("seen")])]
    ).model_copy(update={"agent_context_state": AgentContextStateSnapshot(entries={"unknown.owner": opaque})})
    previous = await RunStorage(store).export(previous)
    frozen = previous.model_dump_json()
    log = []

    async def stream(messages, info):
        assert messages[0].parts[0].content == content
        log.append("model")
        yield "summary"

    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        plugins=(RecordingPlugin("record", log),),
    )
    result = await agent.compact(previous_state=previous, bindings=RunBindings.embedded(state_store=store))
    assert result.status == "completed", result.failure
    assert result.output_or_raise() == "summary|record"
    assert log == ["agent:record", "run:record", "enter:record", "model", "exit:record"]
    namespaces = await RunStorage(store).resolve_context(result.state.agent_context_state)
    assert namespaces.get("unknown.owner") == opaque
    assert previous.model_dump_json() == frozen


async def test_compact_keeps_output_tools_without_business_validation():
    from pydantic import BaseModel

    class Answer(BaseModel):
        value: int

    async def stream(messages, info):
        assert info.output_tools
        yield "summary, not an Answer"

    agent = HarnessBuilder().build(AgentSpec(), output_type=Answer, model=FunctionModel(stream_function=stream))

    @agent._agent.output_validator
    def validate(output):
        pytest.fail("business validators must not see a compaction summary")

    result = await agent.compact(previous_state=_previous())
    assert result.output_or_raise() == "summary, not an Answer"
