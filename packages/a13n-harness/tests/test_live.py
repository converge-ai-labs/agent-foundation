from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest
from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder, HarnessEvent, HarnessRunResultEvent, HarnessState
from a13n_harness.usage import ModelUsageRecord, UsageSnapshot
from pydantic_ai.messages import ModelMessage, ModelResponse, RealtimeTurnCompleteEvent, SpeechPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.realtime import RealtimeModel, RealtimeModelSettings
from pydantic_ai.realtime.codec import (
    OutputTranscript,
    RealtimeCodecEvent,
    RealtimeConnection,
    RealtimeInput,
    ResponseDone,
    SessionUsage,
)
from pydantic_ai.usage import RequestUsage, UsageLimits

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
        super().__init__(profile={"supports_session_seeding": True})
        self.connection = _Connection()
        self.messages: Sequence[ModelMessage] = ()
        self.parameters: ModelRequestParameters | None = None
        self.opened = False
        self.closed = False

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
            yield self.connection
        finally:
            self.closed = True


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
