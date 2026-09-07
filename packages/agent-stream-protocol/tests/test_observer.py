from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest
from a13n_harness import (
    AgentStreamEventProtocol,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessStreamEvent,
    RunBindings,
    SafeFailure,
)
from a13n_stream_protocol import AguiObservationError, HarnessAguiObserver
from ag_ui.core import Event
from ag_ui.core.events import (
    CustomEvent,
    ReasoningEncryptedValueEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from pydantic import BaseModel, Field, TypeAdapter
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import (
    CapabilityEvent,
    EnqueuedMessagesEvent,
    FinalResultEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import RunUsage
from pydantic_core import PydanticSerializationError

_OCCURRED_AT = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)


@dataclass(kw_only=True)
class ExternalCapabilityProgressEvent(CapabilityEvent, namespace="test.external"):
    progress: int


@dataclass(frozen=True, slots=True)
class ExtendedAgentStreamEvent:
    event_kind: str = "capability"
    kind: str = "user.demo.progress"
    value: int = 1


class AliasedExtendedAgentStreamEvent(BaseModel):
    event_kind: str = "capability"
    value: int = Field(default=1, serialization_alias="progressValue")


class UnserializableAgentStreamEvent:
    event_kind = "capability"


def _event(
    sequence: int,
    event: Any,
    *,
    thread_id: str = "thread-1",
    run_id: str = "run-1",
) -> HarnessEvent:
    return HarnessEvent(
        thread_id=thread_id,
        run_id=run_id,
        sequence=sequence,
        occurred_at=_OCCURRED_AT,
        event=event,
    )


def _result_event(
    sequence: int,
    result: HarnessRunResult[Any],
) -> HarnessRunResultEvent[Any]:
    return HarnessRunResultEvent(
        thread_id=result.thread_id,
        run_id=result.run_id,
        sequence=sequence,
        occurred_at=_OCCURRED_AT,
        result=result,
    )


async def _history(*items: HarnessStreamEvent[Any]) -> AsyncIterator[HarnessStreamEvent[Any]]:
    for item in items:
        yield item


def test_capability_event_is_recorded_as_generic_custom_event() -> None:
    source = ExternalCapabilityProgressEvent(
        capability_id="external-capability",
        tool_call_id="call-1",
        tool_name="external_tool",
        progress=2,
    )

    events = HarnessAguiObserver().observe(_event(0, source))

    assert len(events) == 1
    assert isinstance(events[0], CustomEvent)
    assert events[0].name == "test.external.external_capability_progress"
    assert events[0].value == {
        "thread_id": "thread-1",
        "run_id": "run-1",
        "sequence": 0,
        "occurred_at": _OCCURRED_AT.isoformat(),
        "event": {
            "kind": "test.external.external_capability_progress",
            "capability_id": "external-capability",
            "tool_call_id": "call-1",
            "tool_name": "external_tool",
            "event_kind": "capability",
            "progress": 2,
        },
    }


def test_compaction_summary_preserves_full_native_content_and_operation() -> None:
    from a13n_harness.capabilities import CompactionSummaryEvent

    summary = "Complete summary line.\n" * 4000
    events = HarnessAguiObserver().observe(
        _event(0, CompactionSummaryEvent(operation_id="compaction-1", summary=summary))
    )
    from a13n_stream_protocol import CustomEventAssembler

    assembler = CustomEventAssembler()
    assembled = []
    for event in events:
        assert isinstance(event, CustomEvent)
        assert len(event.model_dump_json().encode()) < 64 * 1024
        result = assembler.accept(event.model_dump(mode="json"))
        if result is not None:
            assembled.append(result)
    assert len(assembled) == 1
    assert assembled[0]["name"] == "a13n.context.compaction_summary"
    assert assembled[0]["value"]["event"]["summary"] == summary
    assert assembled[0]["value"]["event"]["operation_id"] == "compaction-1"


def test_extended_agent_stream_event_uses_generic_custom_event() -> None:
    source = ExtendedAgentStreamEvent()
    assert isinstance(source, AgentStreamEventProtocol)

    events = HarnessAguiObserver().observe(_event(0, source))

    assert len(events) == 1
    assert isinstance(events[0], CustomEvent)
    assert events[0].name == "a13n.pydantic_ai.capability"
    assert events[0].value == {
        "thread_id": "thread-1",
        "run_id": "run-1",
        "sequence": 0,
        "occurred_at": _OCCURRED_AT.isoformat(),
        "event": {
            "event_kind": "capability",
            "kind": "user.demo.progress",
            "value": 1,
        },
    }


def test_extended_agent_stream_event_preserves_serialization_aliases() -> None:
    event = HarnessAguiObserver().observe(_event(0, AliasedExtendedAgentStreamEvent()))[0]

    assert isinstance(event, CustomEvent)
    assert event.value["event"] == {
        "event_kind": "capability",
        "progressValue": 1,
    }


def test_unserializable_agent_stream_event_fails_atomically() -> None:
    observer = HarnessAguiObserver()

    with pytest.raises(PydanticSerializationError):
        observer.observe(_event(0, UnserializableAgentStreamEvent()))

    assert observer.thread_id is None
    assert observer.run_id is None
    assert observer.snapshot() == ()


def test_tool_extra_event_uses_directly_subscribable_custom_name() -> None:
    source = HarnessExtensionEvent(
        kind="tool",
        payload={
            "type": "tool_extra",
            "tool_call_id": "call-1",
            "tool_name": "move",
            "tool_id": "filesystem.move",
            "name": "filesystem.changed",
            "value": {
                "changes": [
                    {"path": "source.txt", "action": "moved", "destination": "target.txt"},
                ]
            },
        },
    )

    event = HarnessAguiObserver().observe(_event(0, source))[0]

    assert isinstance(event, CustomEvent)
    assert event.name == "a13n.harness.tool.filesystem.changed"
    assert event.value["event"] == source.model_dump(mode="json")


def test_enqueued_messages_event_preserves_native_steering_observation() -> None:
    source = EnqueuedMessagesEvent(
        enqueue_id="enqueue-1",
        messages=(ModelRequest(parts=[UserPromptPart(content="change direction")]),),
    )

    event = HarnessAguiObserver().observe(_event(0, source))[0]

    assert isinstance(event, CustomEvent)
    assert event.name == "a13n.pydantic_ai.enqueued_messages"
    assert event.value["event"]["enqueue_id"] == "enqueue-1"
    assert event.value["event"]["event_kind"] == "enqueued_messages"
    assert event.value["event"]["messages"][0]["parts"][0]["content"] == "change direction"


def test_text_lifecycle_uses_harness_request_identity_and_accumulates() -> None:
    observer = HarnessAguiObserver()

    lifecycle = observer.observe(
        _event(
            0,
            HarnessExtensionEvent(
                kind="lifecycle",
                payload={
                    "type": "model_request_started",
                    "request_id": "model-request-3",
                    "request_index": 2,
                    "message_count": 1,
                },
            ),
        )
    )
    started = observer.observe(_event(1, PartStartEvent(index=0, part=TextPart("hel"))))
    delta = observer.observe(_event(2, PartDeltaEvent(index=0, delta=TextPartDelta("lo"))))
    ended = observer.observe(_event(3, PartEndEvent(index=0, part=TextPart("hello"))))

    assert len(lifecycle) == 1
    assert isinstance(lifecycle[0], CustomEvent)
    assert lifecycle[0].name == "a13n.harness.lifecycle"
    assert lifecycle[0].value == {
        "thread_id": "thread-1",
        "run_id": "run-1",
        "sequence": 0,
        "occurred_at": _OCCURRED_AT.isoformat(),
        "event": {
            "schema_version": "1",
            "kind": "lifecycle",
            "payload": {
                "type": "model_request_started",
                "request_id": "model-request-3",
                "request_index": 2,
                "message_count": 1,
            },
        },
    }

    assert [type(event) for event in started] == [TextMessageStartEvent, TextMessageContentEvent]
    message_id = "run-1:request-2:part-0:text"
    assert started[0].message_id == message_id
    assert started[0].timestamp == 1767323045678
    assert started[1].delta == "hel"
    assert isinstance(delta[0], TextMessageContentEvent)
    assert delta[0].message_id == message_id
    assert delta[0].delta == "lo"
    assert isinstance(ended[0], TextMessageEndEvent)
    assert ended[0].message_id == message_id
    assert len(observer.snapshot()) == 5
    assert observer.thread_id == "thread-1"
    assert observer.run_id == "run-1"


def test_reasoning_tool_and_tool_result_use_standard_agui_events() -> None:
    observer = HarnessAguiObserver()

    reasoning_start = observer.observe(_event(0, PartStartEvent(index=0, part=ThinkingPart("why", signature="sig-a"))))
    reasoning_delta = observer.observe(
        _event(
            1,
            PartDeltaEvent(
                index=0,
                delta=ThinkingPartDelta(content_delta=" now", signature_delta="sig-b"),
            ),
        )
    )
    reasoning_end = observer.observe(
        _event(2, PartEndEvent(index=0, part=ThinkingPart("why now", signature="sig-final")))
    )
    tool_start = observer.observe(
        _event(
            3,
            PartStartEvent(
                index=1,
                part=ToolCallPart(tool_name="sea", args=None, tool_call_id="call-1"),
            ),
        )
    )
    tool_delta = observer.observe(
        _event(
            4,
            PartDeltaEvent(
                index=1,
                delta=ToolCallPartDelta(tool_name_delta="rch", args_delta='{"q":"x"}'),
            ),
        )
    )
    tool_end = observer.observe(
        _event(
            5,
            PartEndEvent(
                index=1,
                part=ToolCallPart(tool_name="search", args={"q": "x"}, tool_call_id="call-1"),
            ),
        )
    )
    tool_result = observer.observe(
        _event(
            6,
            FunctionToolResultEvent(ToolReturnPart(tool_name="search", content={"found": 1}, tool_call_id="call-1")),
        )
    )

    assert [type(event) for event in reasoning_start] == [
        ReasoningMessageStartEvent,
        ReasoningMessageContentEvent,
        ReasoningEncryptedValueEvent,
    ]
    assert [type(event) for event in reasoning_delta] == [
        ReasoningMessageContentEvent,
        ReasoningEncryptedValueEvent,
    ]
    assert [type(event) for event in reasoning_end] == [ReasoningMessageEndEvent]
    assert isinstance(tool_start[0], CustomEvent)
    assert tool_start[0].name == "a13n.pydantic_ai.part_start"
    assert isinstance(tool_delta[0], CustomEvent)
    assert tool_delta[0].name == "a13n.pydantic_ai.part_delta"
    assert [type(event) for event in tool_end] == [ToolCallStartEvent, ToolCallArgsEvent, ToolCallEndEvent]
    assert tool_end[0].tool_call_name == "search"
    assert tool_end[1].delta == '{"q":"x"}'
    assert isinstance(tool_result[0], ToolCallResultEvent)
    assert tool_result[0].message_id == "call-1:result"
    assert tool_result[0].content == '{"found":1}'


def test_complete_tool_arguments_use_tool_call_args_event() -> None:
    observer = HarnessAguiObserver()

    started = observer.observe(
        _event(
            0,
            PartStartEvent(
                index=0,
                part=ToolCallPart(tool_name="search", args={"a": 1}, tool_call_id="call-1"),
            ),
        )
    )
    observer.observe(
        _event(
            1,
            PartDeltaEvent(index=0, delta=ToolCallPartDelta(args_delta={"b": 2})),
        )
    )
    events = observer.observe(
        _event(
            2,
            PartEndEvent(
                index=0,
                part=ToolCallPart(tool_name="search", args={"a": 1, "b": 2}, tool_call_id="call-1"),
            ),
        )
    )

    assert isinstance(started[0], CustomEvent)
    assert [type(event) for event in events] == [ToolCallStartEvent, ToolCallArgsEvent, ToolCallEndEvent]
    assert events[1].delta == '{"a":1,"b":2}'


def test_non_success_and_retry_tool_results_use_custom_fallback() -> None:
    observer = HarnessAguiObserver()

    failed = observer.observe(
        _event(
            0,
            FunctionToolResultEvent(
                ToolReturnPart(
                    tool_name="search",
                    content="failed",
                    tool_call_id="call-failed",
                    outcome="failed",
                )
            ),
        )
    )[0]
    retry = observer.observe(
        _event(
            1,
            FunctionToolResultEvent(
                RetryPromptPart(
                    content="try again",
                    tool_name="search",
                    tool_call_id="call-retry",
                )
            ),
        )
    )[0]

    assert isinstance(failed, CustomEvent)
    assert failed.name == "a13n.pydantic_ai.function_tool_result"
    assert failed.value["event"]["part"]["outcome"] == "failed"
    assert isinstance(retry, CustomEvent)
    assert retry.value["event"]["part"]["part_kind"] == "retry-prompt"


def test_unmapped_public_events_fall_back_to_namespaced_custom_events() -> None:
    observer = HarnessAguiObserver()

    pydantic_event = observer.observe(_event(0, FinalResultEvent(tool_name="finalize", tool_call_id="call-final")))[0]
    extension_event = observer.observe(
        _event(
            1,
            HarnessExtensionEvent(
                kind="context",
                payload={"type": "environment_mount_set_changed", "sequence": 3},
            ),
        )
    )[0]

    assert isinstance(pydantic_event, CustomEvent)
    assert pydantic_event.name == "a13n.pydantic_ai.final_result"
    assert pydantic_event.value["event"] == {
        "tool_name": "finalize",
        "tool_call_id": "call-final",
        "event_kind": "final_result",
    }
    assert isinstance(extension_event, CustomEvent)
    assert extension_event.name == "a13n.harness.context"
    assert extension_event.value["sequence"] == 1


def test_processor_can_drop_or_replace_content_before_accumulation() -> None:
    def processor(source: Any, event: Event) -> Event | None:
        del source
        if isinstance(event, CustomEvent):
            return None
        if isinstance(event, TextMessageContentEvent):
            return event.model_copy(update={"delta": event.delta.upper()})
        return event

    observer = HarnessAguiObserver(processor=processor)
    assert observer.observe(_event(0, HarnessExtensionEvent(kind="diagnostic", payload={"type": "debug"}))) == ()
    events = observer.observe(_event(1, PartStartEvent(index=0, part=TextPart("hello"))))

    assert len(events) == 2
    assert isinstance(events[1], TextMessageContentEvent)
    assert events[1].delta == "HELLO"
    assert len(observer.snapshot()) == 2


def test_orphan_delta_is_normalized_and_conflicting_part_is_atomic() -> None:
    observer = HarnessAguiObserver()

    events = observer.observe(_event(0, PartDeltaEvent(index=0, delta=TextPartDelta("hello"))))
    assert [type(event) for event in events] == [TextMessageStartEvent, TextMessageContentEvent]
    before = observer.snapshot()

    with pytest.raises(AguiObservationError, match="part kind changed"):
        observer.observe(
            _event(
                1,
                PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta="conflict")),
            )
        )
    assert observer.snapshot() == before


def test_observe_is_atomic_when_processor_returns_invalid_replacement() -> None:
    reject_content = True

    def processor(source: Any, event: Event) -> Event | None:
        nonlocal reject_content
        del source
        if reject_content and isinstance(event, TextMessageContentEvent):
            reject_content = False
            return CustomEvent(name="invalid", value={})
        return event

    observer = HarnessAguiObserver(processor=processor)
    source = _event(0, PartStartEvent(index=0, part=TextPart("hello")))

    with pytest.raises(AguiObservationError, match="changed the AG-UI event type"):
        observer.observe(source)

    assert observer.snapshot() == ()
    assert observer.thread_id is None
    assert observer.run_id is None

    events = observer.observe(source)
    assert [type(event) for event in events] == [TextMessageStartEvent, TextMessageContentEvent]


def test_observe_is_atomic_when_processor_raises() -> None:
    def processor(source: Any, event: Event) -> Event | None:
        del source, event
        raise RuntimeError("processor failed")

    observer = HarnessAguiObserver(processor=processor)

    with pytest.raises(RuntimeError, match="processor failed"):
        observer.observe(_event(0, FinalResultEvent(tool_name=None, tool_call_id=None)))
    assert observer.snapshot() == ()
    assert observer.run_id is None


def test_processor_cannot_change_source_correlation() -> None:
    def processor(source: Any, event: Event) -> Event | None:
        del source
        if isinstance(event, TextMessageStartEvent):
            return event.model_copy(update={"message_id": "other-message"})
        return event

    observer = HarnessAguiObserver(processor=processor)

    with pytest.raises(AguiObservationError, match="changed structural field message_id"):
        observer.observe(_event(0, PartStartEvent(index=0, part=TextPart(""))))
    assert observer.snapshot() == ()


def test_processor_cannot_rewrite_custom_name_or_message_role() -> None:
    def rename_custom(source: Any, event: Event) -> Event | None:
        del source
        if isinstance(event, CustomEvent):
            return event.model_copy(update={"name": "other.event"})
        return event

    with pytest.raises(AguiObservationError, match="structural field name"):
        HarnessAguiObserver(processor=rename_custom).observe(
            _event(0, FinalResultEvent(tool_name=None, tool_call_id=None))
        )

    def rewrite_custom_value(source: Any, event: Event) -> Event | None:
        del source
        if isinstance(event, CustomEvent):
            value = {
                **event.value,
                "event": {**event.value["event"], "tool_call_id": "other-call"},
            }
            return event.model_copy(update={"value": value})
        return event

    with pytest.raises(AguiObservationError, match="structural field value"):
        HarnessAguiObserver(processor=rewrite_custom_value).observe(
            _event(0, FinalResultEvent(tool_name=None, tool_call_id="call-final"))
        )

    def change_role(source: Any, event: Event) -> Event | None:
        del source
        if isinstance(event, TextMessageStartEvent):
            return event.model_copy(update={"role": "user"})
        return event

    with pytest.raises(AguiObservationError, match="structural field role"):
        HarnessAguiObserver(processor=change_role).observe(_event(0, PartStartEvent(index=0, part=TextPart(""))))


def test_one_observer_rejects_another_harness_run_without_mutating_snapshot() -> None:
    observer = HarnessAguiObserver()
    observer.observe(_event(0, FinalResultEvent(tool_name=None, tool_call_id=None)))
    before = observer.snapshot()

    with pytest.raises(AguiObservationError, match="Harness Run correlation changed"):
        observer.observe(
            _event(
                0,
                FinalResultEvent(tool_name=None, tool_call_id=None),
                run_id="run-2",
            )
        )

    assert observer.snapshot() == before


def test_returned_events_and_snapshots_are_detached() -> None:
    observer = HarnessAguiObserver()
    returned = observer.observe(_event(0, PartStartEvent(index=0, part=TextPart(""))))
    assert isinstance(returned[0], TextMessageStartEvent)
    returned[0].message_id = "mutated"

    snapshot = observer.snapshot()
    assert isinstance(snapshot[0], TextMessageStartEvent)
    assert snapshot[0].message_id != "mutated"
    snapshot[0].message_id = "mutated-again"
    assert observer.snapshot()[0].message_id != "mutated-again"


@pytest.mark.anyio
async def test_resume_rebuilds_history_and_continues_through_observe() -> None:
    history = (
        _event(0, PartStartEvent(index=0, part=TextPart("hel"))),
        _event(1, PartDeltaEvent(index=0, delta=TextPartDelta("lo"))),
    )
    live = _event(2, PartEndEvent(index=0, part=TextPart("hello")))
    observer = HarnessAguiObserver()

    resumed = await observer.resume(_history(*history))
    continued = observer.observe(live)

    expected = HarnessAguiObserver()
    for item in history:
        expected.observe(item)
    expected_continued = expected.observe(live)

    assert resumed is None
    assert continued == expected_continued
    assert observer.snapshot() == expected.snapshot()
    assert observer.thread_id == "thread-1"
    assert observer.run_id == "run-1"


@pytest.mark.anyio
async def test_resume_replays_processor_without_returning_historical_events() -> None:
    def processor(source: Any, event: Event) -> Event | None:
        del source
        if isinstance(event, TextMessageContentEvent):
            return event.model_copy(update={"delta": event.delta.upper()})
        return event

    observer = HarnessAguiObserver(processor=processor)

    assert await observer.resume(_history(_event(0, PartStartEvent(index=0, part=TextPart("hello"))))) is None
    snapshot = observer.snapshot()
    assert len(snapshot) == 2
    assert isinstance(snapshot[1], TextMessageContentEvent)
    assert snapshot[1].delta == "HELLO"


@pytest.mark.anyio
async def test_resume_is_atomic_when_history_iteration_fails() -> None:
    async def failing_history() -> AsyncIterator[HarnessStreamEvent[Any]]:
        yield _event(0, PartStartEvent(index=0, part=TextPart("partial")))
        raise RuntimeError("history unavailable")

    observer = HarnessAguiObserver()

    with pytest.raises(RuntimeError, match="history unavailable"):
        await observer.resume(failing_history())

    assert observer.snapshot() == ()
    assert observer.thread_id is None
    assert observer.run_id is None
    assert observer.observe(_event(0, PartStartEvent(index=0, part=TextPart("retry"))))


@pytest.mark.anyio
async def test_resume_is_atomic_when_history_changes_run() -> None:
    observer = HarnessAguiObserver()

    with pytest.raises(AguiObservationError, match="Harness Run correlation changed"):
        await observer.resume(
            _history(
                _event(0, PartStartEvent(index=0, part=TextPart("first"))),
                _event(0, PartStartEvent(index=0, part=TextPart("second")), run_id="run-2"),
            )
        )

    assert observer.snapshot() == ()
    assert observer.thread_id is None
    assert observer.run_id is None


@pytest.mark.anyio
async def test_resume_cancellation_leaves_observer_fresh() -> None:
    entered = anyio.Event()

    async def blocked_history() -> AsyncIterator[HarnessStreamEvent[Any]]:
        yield _event(0, PartStartEvent(index=0, part=TextPart("partial")))
        entered.set()
        await anyio.sleep_forever()

    observer = HarnessAguiObserver()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(observer.resume, blocked_history())
        await entered.wait()
        task_group.cancel_scope.cancel()

    assert observer.snapshot() == ()
    assert observer.thread_id is None
    assert observer.run_id is None

    await observer.resume(_history(_event(0, PartStartEvent(index=0, part=TextPart("retry")))))
    assert observer.thread_id == "thread-1"
    assert observer.run_id == "run-1"
    assert observer.snapshot()


@pytest.mark.anyio
async def test_resume_requires_a_fresh_observer_even_after_empty_history() -> None:
    observed = HarnessAguiObserver()
    observed.observe(_event(0, FinalResultEvent(tool_name=None, tool_call_id=None)))
    with pytest.raises(AguiObservationError, match="requires a fresh observer"):
        await observed.resume(_history())

    resumed = HarnessAguiObserver()
    await resumed.resume(_history())
    with pytest.raises(AguiObservationError, match="requires a fresh observer"):
        await resumed.resume(_history())

    assert resumed.observe(_event(0, FinalResultEvent(tool_name=None, tool_call_id=None)))


@pytest.mark.anyio
async def test_resume_rejects_concurrent_observation_and_resumption() -> None:
    entered = anyio.Event()
    release = anyio.Event()

    async def waiting_history() -> AsyncIterator[HarnessStreamEvent[Any]]:
        entered.set()
        await release.wait()
        yield _event(0, PartStartEvent(index=0, part=TextPart("history")))

    observer = HarnessAguiObserver()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(observer.resume, waiting_history())
        await entered.wait()

        assert observer.snapshot() == ()
        assert observer.thread_id is None
        assert observer.run_id is None
        with pytest.raises(AguiObservationError, match="while observer resumption is in progress"):
            observer.observe(_event(0, PartStartEvent(index=0, part=TextPart("live"))))
        with pytest.raises(AguiObservationError, match="already in progress"):
            await observer.resume(_history())

        release.set()

    assert observer.thread_id == "thread-1"
    assert observer.run_id == "run-1"
    assert observer.snapshot()


@pytest.mark.anyio
async def test_real_harness_stream_observes_lifecycle_text_and_terminal_events() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "hello"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )
    observer = HarnessAguiObserver()
    terminal: HarnessRunResultEvent[str] | None = None

    async with executable.stream("start", bindings=RunBindings.embedded()) as run_stream:
        async for item in run_stream:
            observer.observe(item)
            if isinstance(item, HarnessRunResultEvent):
                terminal = item

    assert terminal is not None
    events = observer.snapshot()
    custom_names = [event.name for event in events if isinstance(event, CustomEvent)]
    assert custom_names.count("a13n.harness.lifecycle") == 2
    assert any(isinstance(event, TextMessageStartEvent) for event in events)
    assert any(isinstance(event, TextMessageContentEvent) and event.delta == "hello" for event in events)
    assert any(isinstance(event, TextMessageEndEvent) for event in events)
    assert not any(isinstance(event, RunStartedEvent) for event in events)
    assert isinstance(events[-1], RunFinishedEvent)
    assert events[-1].thread_id == terminal.thread_id
    assert events[-1].run_id == terminal.run_id
    assert events[-1].result == "hello"
    serialized = [TypeAdapter(Event).dump_python(event, mode="json", by_alias=True) for event in events]
    assert all(isinstance(event, dict) for event in serialized)


@pytest.mark.anyio
async def test_terminal_statuses_map_from_explicit_harness_results() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "state"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )
    completed: HarnessRunResult[str] | None = None
    async with executable.stream("start", bindings=RunBindings.embedded()) as run_stream:
        async for item in run_stream:
            if isinstance(item, HarnessRunResultEvent):
                completed = item.result
    assert completed is not None
    assert completed.state is not None

    suspended = HarnessRunResult(
        thread_id=completed.thread_id,
        run_id="run-suspended",
        status="suspended",
        output=None,
        state=completed.state,
        usage=RunUsage(),
        suspend_reason="deferred",
        deferred=DeferredToolRequests(
            calls=[ToolCallPart(tool_name="external", args={"x": 1}, tool_call_id="call-external")],
            approvals=[ToolCallPart(tool_name="approve", args={}, tool_call_id="call-approval")],
        ),
    )
    failed = HarnessRunResult(
        thread_id=completed.thread_id,
        run_id="run-failed",
        status="failed",
        output=None,
        state=None,
        usage=RunUsage(input_tokens=2, output_tokens=3),
        failure=SafeFailure(code="model_failed", message="The model failed."),
    )
    cancelled = HarnessRunResult(
        thread_id=completed.thread_id,
        run_id="run-cancelled",
        status="cancelled",
        output=None,
        state=None,
        usage=RunUsage(),
    )

    suspended_event = HarnessAguiObserver().observe(_result_event(0, suspended))[0]
    failed_event = HarnessAguiObserver().observe(_result_event(0, failed))[0]
    cancelled_event = HarnessAguiObserver().observe(_result_event(0, cancelled))[0]

    class OpaqueOutput:
        pass

    opaque = HarnessRunResult(
        thread_id=completed.thread_id,
        run_id="run-opaque",
        status="completed",
        output=OpaqueOutput(),
        state=completed.state,
        usage=RunUsage(),
    )
    opaque_event = HarnessAguiObserver().observe(_result_event(0, opaque))[0]

    assert isinstance(suspended_event, CustomEvent)
    assert suspended_event.name == "a13n.harness.run_result"
    deferred = suspended_event.value["event"]["deferred"]
    assert deferred["calls"][0]["tool_call_id"] == "call-external"
    assert deferred["approvals"][0]["tool_call_id"] == "call-approval"
    assert isinstance(failed_event, RunErrorEvent)
    assert failed_event.code == "model_failed"
    assert failed_event.message == "The model failed."
    assert failed_event.usage is not None
    assert failed_event.usage[0].total_tokens == 5
    assert isinstance(cancelled_event, RunErrorEvent)
    assert cancelled_event.code == "run_cancelled"
    assert isinstance(opaque_event, RunFinishedEvent)
    assert opaque_event.result is None
    assert opaque_event.raw_event["result_omitted"] is True
    TypeAdapter(Event).dump_json(opaque_event)


def test_input_projection_preserves_visibility_without_media_payloads() -> None:
    from a13n_harness.model_context import ModelInputEvent, user_prompt_content
    from pydantic_ai.messages import BinaryContent, TextContent

    prompt = UserPromptPart(
        [
            "AGENTS.md is my actual question",
            TextContent(
                "private guidance", metadata={"display": False, "source_id": "test.guidance", "private": "omit"}
            ),
            BinaryContent(data=b"x" * (2 * 1024 * 1024), media_type="image/png"),
        ]
    )
    source = ModelInputEvent(content=user_prompt_content(prompt))
    observer = HarnessAguiObserver()
    events = observer.observe(_event(0, source))
    assert len(events) == 9
    bodies = [event.model_dump(mode="json") for event in events]
    for index, body in enumerate(bodies):
        assert body["role"] == "user"
        assert body["metadata"]["display"] is (index // 3 != 1)
        assert "private" not in body["metadata"]
    assert bodies[4]["delta"] == "private guidance"
    assert bodies[7]["delta"] == "[BinaryContent]"
    assert len(TypeAdapter(list[Event]).dump_json(list(events))) < 4096
    assert observer.snapshot() == events

    def show_hidden(_source, event):
        if isinstance(event, TextMessageContentEvent):
            event.model_extra["metadata"]["display"] = True
        return event

    with pytest.raises(AguiObservationError):
        HarnessAguiObserver(processor=show_hidden).observe(_event(0, source))
