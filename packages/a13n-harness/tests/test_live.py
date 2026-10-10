from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    AgentContext,
    AgentDefinition,
    AgentSpec,
    HarnessBuilder,
    HarnessEvent,
    HarnessRunResultEvent,
    HarnessState,
    RunBindings,
    RunCleanupError,
)
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.model_context import (
    ModelContextBlock,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextRequestKind,
)
from a13n_harness.pricing import AbstractModelCostCapability, ModelCostInput, ModelCostQuote
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from a13n_harness.usage import ModelUsageRecord, UsageSnapshot
from pydantic_ai import CallDeferred, RunContext, Tool, ToolReturn
from pydantic_ai.capabilities import AbstractCapability, Capability, HandleDeferredToolCalls
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import (
    BinaryAudio,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RealtimeSessionErrorEvent,
    RealtimeTurnCompleteEvent,
    RetryPromptPart,
    SpeechPart,
    SpeechPartDelta,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.realtime import RealtimeError, RealtimeModel, RealtimeModelSettings
from pydantic_ai.realtime.codec import (
    AudioDelta,
    CancelResponse,
    ClearAudio,
    CommitAudio,
    CreateResponse,
    OutputTranscript,
    RealtimeCodecEvent,
    RealtimeConnection,
    RealtimeInput,
    ResponseDone,
    SessionUsage,
    ToolCall,
    ToolResult,
    TruncateOutput,
)
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RequestUsage, RunUsage, UsageLimits

pytestmark = pytest.mark.anyio


class _Connection(RealtimeConnection):
    def __init__(self) -> None:
        self.events: asyncio.Queue[RealtimeCodecEvent] = asyncio.Queue()
        self.sent: asyncio.Queue[RealtimeInput] = asyncio.Queue()

    async def send(self, content: RealtimeInput) -> None:
        await self.sent.put(content)

    async def __aiter__(self) -> AsyncIterator[RealtimeCodecEvent]:
        while True:
            yield await self.events.get()


class _Model(RealtimeModel):
    def __init__(self) -> None:
        super().__init__(
            profile={
                "supports_session_seeding": True,
                "supports_manual_turn_control": True,
                "supports_interruption": True,
                "supports_output_truncation": True,
            }
        )
        self.connection = _Connection()
        self.messages: Sequence[ModelMessage] = ()
        self.parameters: ModelRequestParameters | None = None
        self.opened = False
        self.closed = False
        self.connect_error: Exception | None = None
        self.close_error: Exception | None = None

    @property
    def model_name(self) -> str:
        return "test-live"

    @property
    def system(self) -> str:
        return "test"

    @asynccontextmanager
    async def connect(
        self,
        *,
        messages: Sequence[ModelMessage],
        model_settings: RealtimeModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> AsyncIterator[RealtimeConnection]:
        self.messages = messages
        self.parameters = model_request_parameters
        self.opened = True
        try:
            if self.connect_error is not None:
                raise self.connect_error
            yield self.connection
        finally:
            self.closed = True
            if self.close_error is not None:
                raise self.close_error


async def test_live_closes_with_one_terminal_result_and_portable_speech() -> None:
    executable = HarnessBuilder().build(
        AgentDefinition(output_type=str, agent=AgentSpec(instructions="Work carefully."))
    )
    model = _Model()
    live = executable.live(model=model)
    assert not model.opened
    async with live:
        assert model.opened
        await live.send("Hello", respond=False)
        await model.connection.events.put(OutputTranscript("Welcome", is_final=True))
        await model.connection.events.put(SessionUsage(RequestUsage(input_tokens=3, output_tokens=2)))
        await model.connection.events.put(ResponseDone())
        async for event in live:
            if isinstance(event, HarnessEvent) and isinstance(event.event, RealtimeTurnCompleteEvent):
                await live.close()
            if isinstance(event, HarnessRunResultEvent):
                assert model.closed
                assert event.result.status == "completed"
                assert event.result.output is None
    assert live.result is not None
    state = await live.export_state()
    restored = HarnessState.model_validate_json(state.model_dump_json())
    assert restored.thread_id == live.thread_id
    assert any(
        isinstance(p, SpeechPart) and p.transcript == "Welcome"
        for m in restored.message_history
        if isinstance(m, ModelResponse)
        for p in m.parts
    )


async def test_live_cancel_is_distinct_from_close() -> None:
    executable = HarnessBuilder().build(AgentDefinition(output_type=str, agent=AgentSpec()))
    model = _Model()
    async with executable.live(model=model) as live:
        live.cancel()
        events = [event async for event in live]
    assert model.closed
    assert isinstance(events[-1], HarnessRunResultEvent)
    assert events[-1].result.status == "cancelled"


async def test_live_usage_limit_result_includes_settled_usage() -> None:
    executable = HarnessBuilder().build(AgentDefinition(output_type=str, agent=AgentSpec()))
    model = _Model()
    async with executable.live(model=model, usage_limits=UsageLimits(output_tokens_limit=1)) as live:
        await model.connection.events.put(OutputTranscript("Welcome", is_final=True))
        await model.connection.events.put(SessionUsage(RequestUsage(input_tokens=3, output_tokens=2)))
        await model.connection.events.put(ResponseDone())
        events = [event async for event in live]
    assert model.closed
    assert isinstance(events[-1], HarnessRunResultEvent)
    result = events[-1].result
    assert result.status == "failed"
    assert result.failure is not None and result.failure.code == "usage_limit_exceeded"
    records = [record for record in result.usage_records if isinstance(record, ModelUsageRecord)]
    assert len(records) == 1
    assert records[0].request_usage.input_tokens == 3
    assert records[0].request_usage.output_tokens == 2
    assert result.state is not None
    snapshot = UsageSnapshot.from_state(result.state)
    assert snapshot is not None and snapshot.records == result.usage_records
    assert await live.export_state() == result.state


async def test_live_early_exit_closes_without_terminal_receipt() -> None:
    executable = HarnessBuilder().build(AgentDefinition(output_type=str, agent=AgentSpec()))
    model = _Model()
    async with executable.live(model=model) as live:
        await live.send("Keep this input", respond=False)
    assert model.closed
    assert live.result is None
    state = await live.export_state()
    assert state.thread_id == live.thread_id
    assert state.message_history


async def _collect(events) -> list[Any]:
    return [event async for event in events]


async def _call(model: _Model, name: str, args: str = "{}", *, call_id: str = "call-1") -> ToolResult:
    await model.connection.events.put(ToolCall(call_id, tool_name=name, args=args, response_usage_follows=True))
    await model.connection.events.put(ResponseDone())
    while True:
        sent = await model.connection.sent.get()
        if isinstance(sent, ToolResult):
            assert sent.tool_call_id == call_id
            return sent


class _ContextProjection:
    def __init__(self) -> None:
        self.requests = []

    async def wrap_model_context(self, ctx, request, handler):
        projection = await handler(request)
        self.requests.append(request)
        return ModelContextProjection(
            blocks=(
                *projection.blocks,
                ModelContextBlock(
                    source_id="test.live-context",
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content=f"Live context {len(self.requests)}",
                ),
            )
        )


async def test_live_tool_result_preserves_content_and_refreshes_context_before_send() -> None:
    def lookup() -> ToolReturn:
        return ToolReturn(return_value="found", content="Supporting evidence", metadata={"source": "test"})

    projection = _ContextProjection()
    executable = HarnessBuilder().build(
        AgentDefinition(
            output_type=str,
            agent=AgentSpec(),
            capabilities=(Capability(tools=[lookup]),),
        )
    )
    model = _Model()
    async with executable.live(model=model, bindings=RunBindings.embedded(model_context=projection)) as live:
        consume = asyncio.create_task(_collect(live))
        assert isinstance(model.messages[-1], ModelRequest)
        assert "Live context 1" in (model.messages[-1].instructions or "")
        result = await _call(model, "lookup")
        assert "found" in result.output
        # Harness lowers supplemental tool evidence into the canonical return value.
        assert "Supporting evidence" in result.output
        assert "Live context 2" in str(result.content)
        assert [request.kind for request in projection.requests] == [
            ModelContextRequestKind.INPUT,
            ModelContextRequestKind.TOOL_RESULTS,
        ]
        assert projection.requests[-1].tool_call_ids == ("call-1",)
        await live.close()
        await consume
    assert live.result is not None and live.result.status == "completed"
    returns = [
        part
        for message in live.result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert returns[0].metadata is not None and returns[0].metadata["source"] == "test"


@pytest.mark.parametrize("approved", [True, False])
async def test_live_approval_resolves_inline_without_executing_denied_tool(approved: bool) -> None:
    executed = []
    waiting = asyncio.Event()
    decide = asyncio.Event()

    def change(value: int) -> int:
        executed.append(value)
        return value * 2

    async def approve(ctx: RunContext[AgentContext], requests: DeferredToolRequests) -> DeferredToolResults:
        assert ctx.run_id == live.run_id
        assert len(requests.approvals) == 1
        waiting.set()
        await decide.wait()
        return DeferredToolResults(approvals={request.tool_call_id: approved for request in requests.approvals})

    executable = HarnessBuilder().build(
        AgentDefinition(
            output_type=str,
            agent=AgentSpec(),
            capabilities=(
                Capability(tools=[Tool(change, requires_approval=True)]),
                HandleDeferredToolCalls(approve),
            ),
        )
    )
    model = _Model()
    projection = _ContextProjection()
    async with executable.live(model=model, bindings=RunBindings.embedded(model_context=projection)) as live:
        consume = asyncio.create_task(_collect(live))
        call = asyncio.create_task(_call(model, "change", '{"value": 21}'))
        await waiting.wait()
        assert not executed and not call.done()
        decide.set()
        result = await call
        assert executed == ([21] if approved else [])
        assert len(projection.requests) == (2 if approved else 1)
        assert ("42" in result.output) if approved else ("denied" in result.output.lower())
        await live.close()
        await consume
    assert live.result is not None and live.result.status == "completed"


async def test_live_cancellation_unwinds_waiting_approval() -> None:
    waiting = asyncio.Event()
    cleaned = asyncio.Event()

    def change() -> None:
        raise AssertionError("approval was never granted")

    async def approve(ctx, requests):
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    executable = HarnessBuilder().build(
        AgentDefinition(
            output_type=str,
            agent=AgentSpec(),
            capabilities=(
                Capability(tools=[Tool(change, requires_approval=True)]),
                HandleDeferredToolCalls(approve),
            ),
        )
    )
    model = _Model()
    async with executable.live(model=model) as live:
        consume = asyncio.create_task(_collect(live))
        await model.connection.events.put(ToolCall("call-1", tool_name="change", args="{}"))
        await waiting.wait()
        live.cancel()
        events = await consume
    assert cleaned.is_set() and model.closed
    assert isinstance(events[-1], HarnessRunResultEvent)
    assert events[-1].result.status == "cancelled"


async def test_live_codeact_directory_and_stored_state_survive_new_run() -> None:
    def double(value: int) -> int:
        return value * 2

    tools = CodeActPolicyToolset(wrapped=FunctionToolset([double]), policy=CodeActToolPolicy(tools={"double": True}))
    executable = HarnessBuilder().build(
        AgentDefinition(
            output_type=str,
            agent=AgentSpec(),
            capabilities=(Capability(toolsets=[tools]), CodeActCapability()),
        )
    )
    model = _Model()
    async with executable.live(model=model) as first:
        consume = asyncio.create_task(_collect(first))
        assert isinstance(model.messages[-1], ModelRequest)
        assert "double" in (model.messages[-1].instructions or "")
        result = await _call(
            model,
            "run_code",
            json.dumps({"code": "value = await double(value=21)\nawait store(key='answer', value=value)\nvalue"}),
        )
        assert "42" in result.output
        await first.close()
        await consume
    state = HarnessState.model_validate_json((await first.export_state()).model_dump_json())
    resumed_model = _Model()
    async with executable.live(model=resumed_model, previous_state=state) as second:
        consume = asyncio.create_task(_collect(second))
        assert second.thread_id == first.thread_id and second.run_id != first.run_id
        assert second.context is not first.context
        result = await _call(
            resumed_model, "run_code", json.dumps({"code": "await load(key='answer')"}), call_id="call-2"
        )
        assert "42" in result.output
        await second.close()
        await consume


@pytest.mark.parametrize("retention", ["transcript_only", "all"])
async def test_live_audio_playback_is_separate_from_semantic_events(retention: str) -> None:
    executable = HarnessBuilder().build(AgentDefinition(output_type=str, agent=AgentSpec()))
    model = _Model()
    observed = []
    async with executable.live(
        model=model, audio_retention=retention, bindings=RunBindings.embedded(producer_observer=observed.append)
    ) as live:
        audio = asyncio.create_task(_collect(live.stream_audio()))
        await live.send_audio(b"\x00\x00" * 240)
        sent = await model.connection.sent.get()
        assert isinstance(sent, BinaryAudio) and sent.data == b"\x00\x00" * 240
        await live.commit_audio()
        assert isinstance(await model.connection.sent.get(), CommitAudio)
        await live.clear_audio()
        assert isinstance(await model.connection.sent.get(), ClearAudio)
        await live.create_response()
        assert isinstance(await model.connection.sent.get(), CreateResponse)
        await model.connection.events.put(AudioDelta(b"\x01\x00" * 240))
        await model.connection.events.put(OutputTranscript("Hello", is_final=True))
        await model.connection.events.put(ResponseDone())
        events = []
        async for event in live:
            events.append(event)
            if isinstance(event, HarnessEvent) and isinstance(event.event, RealtimeTurnCompleteEvent):
                await live.close()
        assert b"".join(await audio) == b"\x01\x00" * 240
    assert any(isinstance(event.event, PartEndEvent) for event in observed)
    for event in [*events, *observed]:
        if not isinstance(event, HarnessEvent):
            continue
        native = event.event
        if isinstance(native, PartDeltaEvent) and isinstance(native.delta, SpeechPartDelta):
            assert native.delta.audio_chunk is None
        if isinstance(native, (PartStartEvent, PartEndEvent)) and isinstance(native.part, SpeechPart):
            assert native.part.audio is None
    state = await live.export_state()
    speech = [
        part
        for message in state.message_history
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, SpeechPart)
    ]
    assert bool(speech[0].audio) == (retention == "all")


async def test_live_connect_failure_closes_and_retains_checkpoint() -> None:
    executable = HarnessBuilder().build(AgentDefinition(output_type=str, agent=AgentSpec()))
    model = _Model()
    model.connect_error = RuntimeError("connection rejected")
    live = executable.live(model=model)
    with pytest.raises(RuntimeError, match="connection rejected"):
        async with live:
            raise AssertionError("connection must not succeed")
    assert model.closed
    assert live.result is None
    assert (await live.export_state()).thread_id == live.thread_id


async def test_live_external_result_refreshes_context_before_send() -> None:
    def lookup() -> str:
        raise CallDeferred()

    async def resolve(ctx, requests):
        return DeferredToolResults(
            calls={
                call.tool_call_id: ToolReturn(
                    return_value="external answer", content="External evidence", metadata={"source": "external"}
                )
                for call in requests.calls
            }
        )

    projection = _ContextProjection()
    executable = HarnessBuilder().build(
        AgentDefinition(
            output_type=str,
            agent=AgentSpec(),
            capabilities=(Capability(tools=[lookup]), HandleDeferredToolCalls(resolve)),
        )
    )
    model = _Model()
    async with executable.live(model=model, bindings=RunBindings.embedded(model_context=projection)) as live:
        consume = asyncio.create_task(_collect(live))
        result = await _call(model, "lookup")
        assert "external answer" in result.output
        assert "External evidence" in result.output
        assert "Live context 2" in str(result.content)
        assert len(projection.requests) == 2
        second = await _call(model, "lookup", call_id="call-2")
        assert "Live context 3" in str(second.content)
        assert len(projection.requests) == 3
        await live.close()
        await consume
    assert live.result is not None
    returns = [
        part
        for message in live.result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert returns[0].metadata is not None and returns[0].metadata["source"] == "external"


@pytest.mark.parametrize(
    "retry", [RetryPromptPart(content="Try a different query"), ModelRetry("Try a different query")]
)
async def test_live_external_retry_preserves_native_control_signal(retry: RetryPromptPart | ModelRetry) -> None:
    def lookup() -> str:
        raise CallDeferred()

    async def resolve(ctx, requests):
        return DeferredToolResults(calls={call.tool_call_id: retry for call in requests.calls})

    projection = _ContextProjection()
    executable = HarnessBuilder().build(
        AgentDefinition(
            output_type=str,
            agent=AgentSpec(),
            capabilities=(Capability(tools=[lookup]), HandleDeferredToolCalls(resolve)),
        )
    )
    model = _Model()
    async with executable.live(model=model, bindings=RunBindings.embedded(model_context=projection)) as live:
        consume = asyncio.create_task(_collect(live))
        result = await _call(model, "lookup")
        assert result.output == "Try a different query\n\nFix the errors and try again."
        assert not result.content
        assert len(projection.requests) == 1
        await live.close()
        await consume
    assert live.result is not None
    returns = [
        part
        for message in live.result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, (RetryPromptPart, ToolReturnPart))
    ]
    assert len(returns) == 1 and isinstance(returns[0], RetryPromptPart)
    assert returns[0].tool_name == "lookup" and returns[0].tool_call_id == "call-1"


async def test_live_missing_approval_handler_fails_tool_without_executing_it() -> None:
    def change() -> None:
        raise AssertionError("unapproved tool must not run")

    executable = HarnessBuilder().build(
        AgentDefinition(
            output_type=str,
            agent=AgentSpec(),
            capabilities=(Capability(tools=[Tool(change, requires_approval=True)]),),
        )
    )
    model = _Model()
    async with executable.live(model=model) as live:
        consume = asyncio.create_task(_collect(live))
        result = await _call(model, "change")
        assert "requires approval" in result.output and "cannot be completed" in result.output
        await live.close()
        await consume
    assert live.result is not None and live.result.status == "completed"


async def test_live_active_export_includes_sent_input_without_waiting_for_provider() -> None:
    executable = HarnessBuilder().build(AgentDefinition(output_type=str, agent=AgentSpec()))
    model = _Model()
    async with executable.live(model=model) as live:
        await live.send("Keep this active input", respond=False)
        state = await live.export_state()
        assert "Keep this active input" in state.model_dump_json()
        assert live.result is None


async def test_live_failed_connection_close_has_no_success_receipt() -> None:
    executable = HarnessBuilder().build(AgentDefinition(output_type=str, agent=AgentSpec()))
    model = _Model()
    model.close_error = RuntimeError("close failed")
    live = executable.live(model=model)
    with pytest.raises(RuntimeError, match="close failed"):
        async with live:
            await live.send("Keep input after close failure", respond=False)
            await live.close()
            await _collect(live)
    assert model.closed and live.result is None
    assert "Keep input after close failure" in (await live.export_state()).model_dump_json()


async def test_live_interrupt_truncates_before_cancel_without_ending_run() -> None:
    executable = HarnessBuilder().build(AgentDefinition(output_type=str, agent=AgentSpec()))
    model = _Model()
    async with executable.live(model=model) as live:
        await live.interrupt(played_ms=120)
        truncate = await model.connection.sent.get()
        assert isinstance(truncate, TruncateOutput) and truncate.audio_end_ms == 120
        assert isinstance(await model.connection.sent.get(), CancelResponse)
        assert live.result is None and not model.closed
        await live.close()
        events = await _collect(live)
    assert events[-1].result.status == "completed"


async def test_live_fatal_provider_error_keeps_checkpoint_without_success() -> None:
    executable = HarnessBuilder().build(AgentDefinition(output_type=str, agent=AgentSpec()))
    model = _Model()
    live = executable.live(model=model)
    with pytest.raises(RealtimeError, match="provider failed"):
        async with live:
            await live.send("Retain before failure", respond=False)
            await model.connection.events.put(RealtimeSessionErrorEvent("provider failed", recoverable=False))
            await _collect(live)
    assert model.closed and live.result is None
    assert "Retain before failure" in (await live.export_state()).model_dump_json()


class _FixedCostCapability(AbstractModelCostCapability):
    @property
    def revision(self) -> str:
        return "fixed-v1"

    def quote(self, value: ModelCostInput) -> ModelCostQuote:
        return ModelCostQuote(cost_usd=Decimal("0.125"), source="custom", pricing_revision=self.revision)


class _PauseAtStart(AbstractCapability[AgentContext]):
    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.resume = asyncio.Event()

    async def on_event(self, ctx, *, event):
        if isinstance(event, PartStartEvent):
            self.waiting.set()
            await self.resume.wait()


async def test_live_active_export_does_not_consume_usage_limit_failure() -> None:
    pause = _PauseAtStart()
    executable = HarnessBuilder().build(
        AgentDefinition(output_type=str, agent=AgentSpec(), capabilities=(_FixedCostCapability(), pause))
    )
    model = _Model()
    async with (
        asyncio.timeout(5),
        executable.live(model=model, usage_limits=UsageLimits(cost_limit=Decimal("0.1"))) as live,
    ):
        consume = asyncio.create_task(_collect(live))
        await model.connection.events.put(OutputTranscript("Answer", is_final=True))
        await model.connection.events.put(SessionUsage(RequestUsage(input_tokens=3, output_tokens=2)))
        await model.connection.events.put(ResponseDone())
        await pause.waiting.wait()
        while live.native_usage.requests < 1:
            await asyncio.sleep(0)
        try:
            assert live.usage.requests == 0
            first = await live.export_state()
            second = await live.export_state()
            assert UsageSnapshot.from_state(first) == UsageSnapshot.from_state(second)
            assert live.usage.cost == Decimal("0.125")
        finally:
            pause.resume.set()
        # Normal close must not turn an already exceeded limit into success.
        await live.close()
        events = await consume
    assert model.closed
    result = events[-1].result
    assert result.status == "failed"
    assert result.failure is not None and result.failure.code == "usage_limit_exceeded"
    assert result.usage.cost == Decimal("0.125") and result.usage.requests == 1


class _Reporter:
    def __init__(self) -> None:
        self.snapshots: list[UsageSnapshot] = []

    async def report(self, snapshot: UsageSnapshot) -> None:
        self.snapshots.append(snapshot)


async def test_live_reports_usage_while_active_without_double_counting_or_baseline() -> None:
    executable = HarnessBuilder().build(AgentDefinition(output_type=str, agent=AgentSpec()))
    model = _Model()
    reporter = _Reporter()
    baseline = RunUsage(input_tokens=100, requests=2)
    async with executable.live(
        model=model, usage=baseline, bindings=RunBindings.embedded(usage_reporter=reporter)
    ) as live:
        await model.connection.events.put(OutputTranscript("One", is_final=True))
        await model.connection.events.put(SessionUsage(RequestUsage(input_tokens=3, output_tokens=2)))
        await model.connection.events.put(ResponseDone())
        async for event in live:
            if isinstance(event, HarnessEvent) and isinstance(event.event, RealtimeTurnCompleteEvent):
                assert reporter.snapshots and live.result is None
                first = await live.export_state()
                second = await live.export_state()
                assert UsageSnapshot.from_state(first) == UsageSnapshot.from_state(second)
                assert live.usage.input_tokens == 3 and live.usage.requests == 1
                assert baseline.input_tokens == 103 and baseline.requests == 3
                await live.close()
    assert live.result is not None
    records = [record for record in live.result.usage_records if isinstance(record, ModelUsageRecord)]
    assert len(records) == 1
    assert all(snapshot.records == tuple(records) for snapshot in reporter.snapshots)


async def test_live_session_only_usage_is_exposed_without_fabricating_response_records() -> None:
    executable = HarnessBuilder().build(AgentDefinition(output_type=str, agent=AgentSpec()))
    model = _Model()
    async with executable.live(model=model) as live:
        await model.connection.events.put(
            SessionUsage(RequestUsage(details={"input_transcription_tokens": 7}), response_scoped=False)
        )
        await model.connection.events.put(OutputTranscript("Hello", is_final=True))
        await model.connection.events.put(SessionUsage(RequestUsage(input_tokens=3)))
        await model.connection.events.put(ResponseDone())
        async for event in live:
            if isinstance(event, HarnessEvent) and isinstance(event.event, RealtimeTurnCompleteEvent):
                assert live.native_usage.details["input_transcription_tokens"] == 7
                detached = live.native_usage
                detached.input_tokens = 999
                assert live.native_usage.input_tokens == 3
                assert live.usage.model_usage_coverage == "responses_only"
                assert "input_transcription_tokens" not in live.usage.details
                assert live.usage.requests == 1
                await live.close()
    assert live.result is not None and live.result.usage.model_usage_coverage == "responses_only"
    snapshot = UsageSnapshot.from_state(await live.export_state())
    assert snapshot is not None
    assert snapshot.model_usage_coverage == snapshot.summary.model_usage_coverage == "responses_only"


async def test_live_permission_deny_prevents_tool_execution() -> None:
    def change() -> None:
        raise AssertionError("denied tool must not run")

    executable = HarnessBuilder().build(
        AgentDefinition(
            output_type=str,
            agent=AgentSpec(),
            capabilities=(
                Capability(tools=[change]),
                ToolPermissionsCapability(ToolPermissions(default="deny")),
            ),
        )
    )
    model = _Model()
    async with executable.live(model=model) as live:
        consume = asyncio.create_task(_collect(live))
        result = await _call(model, "change")
        assert "denied" in result.output.lower()
        await live.close()
        await consume
    assert live.result is not None and live.result.status == "completed"


class _CleanupFailurePlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self) -> str:
        return "live-cleanup-failure"

    async def wrap_run(self, exchange, call_next):
        try:
            return await call_next(exchange)
        finally:
            raise RuntimeError("plugin cleanup failed")


async def test_live_plugin_cleanup_failure_withholds_terminal_result() -> None:
    executable = HarnessBuilder().build(
        AgentDefinition(output_type=str, agent=AgentSpec(), plugins=(_CleanupFailurePlugin(),))
    )
    model = _Model()
    live = executable.live(model=model)
    events = []
    with pytest.raises(RunCleanupError) as failure:
        async with live:
            await live.send("Keep before plugin failure", respond=False)
            await live.close()
            async for event in live:
                events.append(event)
    assert model.closed and live.result is None
    assert not any(isinstance(event, HarnessRunResultEvent) for event in events)
    assert failure.value.outcome is not None and failure.value.outcome.output is None
    assert "Keep before plugin failure" in (await live.export_state()).model_dump_json()
