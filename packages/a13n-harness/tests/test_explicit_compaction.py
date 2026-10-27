from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessState, RunBindings
from a13n_harness import AgentSpec as HarnessAgentSpec
from a13n_harness._context_history import _COMPACTION_PROMPT
from a13n_harness.capabilities import (
    CompactionCapability,
    CompactionPolicy,
    HandoffCapability,
    RuntimeContextCapability,
    WorkingStateCapability,
)
from a13n_harness.events import HarnessExtensionEvent
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
    assert result.state.agent_context_state.entries["a13n.compaction"].data["restoration_pending"] is True
    assert _COMPACTION_PROMPT not in str(result.state.message_history)
    # Saving and loading is sufficient: no ordinary Run is needed to finish compact().
    saved = HarnessState.model_validate_json(result.state.model_dump_json())
    resumed = await agent.run("Continue", previous_state=saved)
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
    assert result.failure.details == {"phase": "summary"}
    assert result.state.model_dump_json() == previous.model_dump_json()
    assert "a13n.working-state" not in result.state.agent_context_state.entries


@pytest.mark.parametrize("incomplete_at", ["segment", "response"])
async def test_compact_rejects_nonempty_incomplete_response_before_checkpoint_or_summary_event(incomplete_at):
    from a13n_harness.capabilities.context import CompactionSummaryEvent
    from pydantic_ai.capabilities import AbstractCapability

    summaries = []
    observed = []
    calls = 0

    class Listener(AbstractCapability):
        async def on_event(self, ctx, *, event):
            if isinstance(event, CompactionSummaryEvent):
                summaries.append(event)

        async def after_model_request(self, ctx, *, request_context, response):
            if incomplete_at == "response":
                response.state = "incomplete"
            return response

    class IncompleteModel(FunctionModel):
        @asynccontextmanager
        async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
            async with super().request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as response:
                if incomplete_at == "segment":
                    response.state = "incomplete"
                yield response

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        yield "Partial summary"

    agent = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=IncompleteModel(stream_function=stream), capabilities=(Listener(),)
    )
    previous = _previous()
    frozen = previous.model_dump_json()
    result = await agent.compact(
        previous_state=previous, bindings=RunBindings.embedded(producer_observer=observed.append)
    )
    assert result.status == "failed"
    assert result.failure.code == "compaction_summary_invalid"
    assert result.failure.details == {"phase": "summary"}
    assert result.output is None
    assert result.state.model_dump_json() == frozen
    assert previous.model_dump_json() == frozen
    assert calls == 1
    assert summaries == []
    events = [
        item.event.payload
        for item in observed
        if isinstance(item.event, HarnessExtensionEvent) and item.event.kind == "context"
    ]
    assert [event["type"] for event in events] == ["compaction_started", "compaction_failed"]
    assert events[1]["failed_phase"] == "summary"
    assert events[1]["operation_id"] == events[0]["operation_id"]


@pytest.mark.parametrize("terminal_state", ["complete", "incomplete"])
async def test_compact_checks_terminal_segment_after_native_continuation(terminal_state):
    calls = 0

    class ContinuingModel(FunctionModel):
        @asynccontextmanager
        async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
            state = "suspended" if calls == 0 else terminal_state
            async with super().request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as response:
                response.state = state
                yield response

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        yield "Partial summary" if calls == 1 else "Finished summary"

    agent = HarnessBuilder().build(AgentSpec(), output_type=str, model=ContinuingModel(stream_function=stream))
    previous = _previous()
    result = await agent.compact(previous_state=previous)
    assert calls == 2
    if terminal_state == "complete":
        assert result.status == "completed", result.failure
        assert "Finished summary" in result.output_or_raise()
    else:
        assert result.status == "failed"
        assert result.failure.code == "compaction_summary_invalid"
        assert result.state.model_dump_json() == previous.model_dump_json()


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
    observed = []
    result = await agent.compact(
        previous_state=HarnessState.new(message_history=history),
        bindings=RunBindings.embedded(producer_observer=observed.append),
    )
    assert result.status == "failed"
    assert result.failure.details == {"phase": "preflight"}
    events = [
        item.event.payload
        for item in observed
        if isinstance(item.event, HarnessExtensionEvent) and item.event.kind == "context"
    ]
    assert [event["type"] for event in events] == ["compaction_started", "compaction_failed"]
    assert events[1]["error_code"] == result.failure.code
    assert events[1]["operation_id"] == events[0]["operation_id"]


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
    observed = []
    task = asyncio.create_task(
        agent.compact(previous_state=previous, bindings=RunBindings.embedded(producer_observer=observed.append))
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert previous.model_dump_json() == frozen
    failures = [
        item.event.payload
        for item in observed
        if isinstance(item.event, HarnessExtensionEvent) and item.event.payload.get("type") == "compaction_failed"
    ]
    assert len(failures) == 1
    assert failures[0]["error_code"] == "compaction_cancelled"


async def test_compact_can_compact_a_restored_checkpoint_again():
    async def stream(messages, info):
        yield "summary"

    agent = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream))
    first = await agent.compact(previous_state=_previous())
    restored = await agent.run("Continue", previous_state=first.state)
    second = await agent.compact(previous_state=restored.state)
    assert second.status == "completed", second.failure


@pytest.mark.parametrize("cancel", [False, True])
async def test_compact_defers_restoration_and_ordinary_run_can_retry(cancel):
    from a13n_harness.capabilities.context import ContextRestoredEvent
    from pydantic_ai.capabilities import AbstractCapability

    contexts = []
    calls = []

    class Listener(AbstractCapability):
        async def on_event(self, ctx, *, event):
            if isinstance(event, ContextRestoredEvent):
                contexts.append(ctx)
                if len(contexts) == 1:
                    raise asyncio.CancelledError() if cancel else RuntimeError("listener failed")

    async def stream(messages, info):
        calls.append(deepcopy(messages))
        yield "summary"

    agent = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream), capabilities=(Listener(),)
    )
    compacted = await agent.compact(previous_state=_previous())
    assert compacted.status == "completed", compacted.failure
    assert contexts == []
    state = HarnessState.model_validate_json(compacted.state.model_dump_json())
    marker = state.agent_context_state.entries["a13n.compaction"]
    assert marker.data["restoration_pending"] is True
    frozen = state.model_dump_json()
    retried = await agent.compact(previous_state=state)
    assert retried.status == "failed"
    assert retried.failure.code == "compaction_boundary_pending"
    assert len(calls) == 1
    assert len(contexts) == 0
    assert retried.state.message_history == state.message_history
    assert retried.state.agent_context_state.entries["a13n.compaction"] == marker
    assert state.model_dump_json() == frozen
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await agent.run("continue", previous_state=state)
    else:
        failed = await agent.run("continue", previous_state=state)
        assert failed.status == "failed"
    assert len(contexts) == 1
    assert len(calls) == 1
    assert state.model_dump_json() == frozen
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


async def test_compact_completes_notifications_before_save_and_restores_once():
    from a13n_harness.capabilities.context import CompactionSummaryEvent, ContextRestoredEvent
    from pydantic import BaseModel
    from pydantic_ai.capabilities import AbstractCapability

    notifications = []
    observed = []

    class ListenerState(BaseModel):
        summary: str

    class Listener(AbstractCapability):
        async def on_event(self, ctx, *, event):
            if isinstance(event, CompactionSummaryEvent | ContextRestoredEvent):
                notifications.append(event)
            if isinstance(event, CompactionSummaryEvent):
                await ctx.deps.state.write("test.summary-listener", ListenerState(summary=event.summary), version="1")

    async def stream(messages, info):
        yield "summary"

    agent = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream), capabilities=(Listener(),)
    )
    bindings = RunBindings.embedded(producer_observer=observed.append)
    result = await agent.compact(previous_state=_previous(), bindings=bindings)
    assert result.status == "completed", result.failure
    assert [type(event) for event in notifications] == [CompactionSummaryEvent]
    events = [
        item.event.payload
        for item in observed
        if isinstance(item.event, HarnessExtensionEvent) and item.event.kind == "context"
    ]
    assert [event["type"] for event in events] == ["compaction_started", "compaction_completed"]
    assert events[0]["operation_id"] == events[1]["operation_id"]
    saved = HarnessState.model_validate_json(result.state.model_dump_json())
    assert saved.agent_context_state.entries["test.summary-listener"].data == {"summary": "summary"}
    observed.clear()
    resumed = await agent.run("Continue", previous_state=saved, bindings=bindings)
    assert resumed.status == "completed", resumed.failure
    assert [type(event) for event in notifications] == [CompactionSummaryEvent, ContextRestoredEvent]
    assert notifications[0].operation_id == notifications[1].operation_id
    assert resumed.state.agent_context_state.entries["test.summary-listener"].data == {"summary": "summary"}
    assert not [
        item
        for item in observed
        if isinstance(item.event, HarnessExtensionEvent) and item.event.payload.get("type") == "compaction_completed"
    ]
    assert resumed.state.agent_context_state.entries["a13n.compaction"].data["restoration_pending"] is False


@pytest.mark.parametrize("phase", ["checkpoint", "notification"])
async def test_compact_post_summary_failure_returns_original_checkpoint(phase, monkeypatch):
    from a13n_harness.capabilities.context import CompactionSummaryEvent
    from a13n_harness.context import AgentContext
    from pydantic_ai.capabilities import AbstractCapability

    class Listener(AbstractCapability):
        async def on_event(self, ctx, *, event):
            if phase == "notification" and isinstance(event, CompactionSummaryEvent):
                raise RuntimeError("private listener failure")

    async def fail_export(self, messages):
        raise RuntimeError("private storage failure")

    if phase == "checkpoint":
        monkeypatch.setattr(AgentContext, "export_state", fail_export)

    async def stream(messages, info):
        yield "summary"

    agent = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream), capabilities=(Listener(),)
    )
    previous = _previous()
    observed = []
    result = await agent.compact(
        previous_state=previous, bindings=RunBindings.embedded(producer_observer=observed.append)
    )
    assert result.status == "failed"
    assert result.failure.details == {"phase": phase}
    assert "private" not in result.failure.message
    assert result.state.model_dump_json() == previous.model_dump_json()
    events = [
        item.event.payload
        for item in observed
        if isinstance(item.event, HarnessExtensionEvent) and item.event.kind == "context"
    ]
    assert [event["type"] for event in events] == ["compaction_started", "compaction_failed"]
    assert events[1]["failed_phase"] == phase
