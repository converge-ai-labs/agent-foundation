from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

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
from a13n_harness.usage import RunUsageSummary
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
from pydantic import BaseModel, Field, PlainSerializer, PydanticSchemaGenerationError, TypeAdapter, model_serializer
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
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.output import TextOutput
from pydantic_ai.tools import DeferredToolRequests
from pydantic_core import PydanticSerializationError

_OCCURRED_AT = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)


@dataclass(kw_only=True)
class ExternalCapabilityProgressEvent(CapabilityEvent, namespace="test.external"):
    progress: int


@dataclass(kw_only=True)
class ExternalCapabilityDetailsEvent(CapabilityEvent, namespace="test.external"):
    delta: ThinkingPartDelta


@dataclass(frozen=True, slots=True)
class ExtendedAgentStreamEvent:
    event_kind: str = "capability"
    kind: str = "user.demo.progress"
    value: int = 1


@dataclass
class AliasedDataclassEvent:
    event_kind: str = "external"
    value: Annotated[int, Field(serialization_alias="progressValue")] = 1


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


@pytest.mark.parametrize("event_type", [AliasedExtendedAgentStreamEvent, AliasedDataclassEvent])
def test_extended_agent_stream_event_preserves_serialization_aliases(event_type: type) -> None:
    source = event_type()
    event = HarnessAguiObserver().observe(_event(0, source))[0]

    assert isinstance(event, CustomEvent)
    assert event.value["event"] == {
        "event_kind": source.event_kind,
        "progressValue": 1,
    }


@pytest.mark.parametrize("callback", [False, True])
def test_thinking_metadata_delta_uses_native_json_serializer(callback: bool) -> None:
    def merge_details(existing: dict[str, Any] | None) -> dict[str, Any]:
        pytest.fail("Observation must not execute the provider details callback")

    details = merge_details if callback else {"signature": "provider-signature"}
    delta = ThinkingPartDelta(provider_name="test", provider_details=details)
    source = PartDeltaEvent(index=0, delta=delta)
    observer = HarnessAguiObserver()
    observer.observe(_event(0, PartStartEvent(index=0, part=ThinkingPart("reasoning"))))

    event = observer.observe(_event(1, source))[0]

    assert isinstance(event, CustomEvent)
    assert event.name == "a13n.pydantic_ai.part_delta"
    assert event.value == {
        "thread_id": "thread-1",
        "run_id": "run-1",
        "sequence": 1,
        "occurred_at": _OCCURRED_AT.isoformat(),
        "event": {
            "event_kind": "part_delta",
            "index": 0,
            "delta": {
                "part_delta_kind": "thinking",
                "content_delta": None,
                "signature_delta": None,
                "provider_name": "test",
                "provider_details": None if callback else details,
            },
        },
    }
    TypeAdapter(Event).dump_json(event)
    assert delta.provider_details is details
    following = observer.observe(_event(2, PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=" more"))))
    assert len(following) == 1
    assert isinstance(following[0], ReasoningMessageContentEvent)
    assert following[0].delta == " more"


def test_capability_event_uses_nested_field_serializer() -> None:
    def merge_details(existing: dict[str, Any] | None) -> dict[str, Any]:
        pytest.fail("Observation must not execute the provider details callback")

    delta = ThinkingPartDelta(provider_name="test", provider_details=merge_details)
    source = ExternalCapabilityDetailsEvent(capability_id="external-capability", delta=delta)
    event = HarnessAguiObserver().observe(_event(0, source))[0]

    assert isinstance(event, CustomEvent)
    assert event.name == source.kind
    assert event.value["event"]["capability_id"] == "external-capability"
    assert event.value["event"]["delta"]["provider_details"] is None
    assert delta.provider_details is merge_details
    TypeAdapter(Event).dump_json(event)


@pytest.mark.parametrize(
    ("source", "error"),
    [
        (UnserializableAgentStreamEvent(), PydanticSchemaGenerationError),
        (ExtendedAgentStreamEvent(value=lambda: None), PydanticSerializationError),
    ],
)
def test_unserializable_agent_stream_event_fails_atomically(source: object, error: type[Exception]) -> None:
    observer = HarnessAguiObserver()

    with pytest.raises(error):
        observer.observe(_event(0, source))

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


@pytest.mark.parametrize("first_event", ["start", "delta", "end"])
def test_reasoning_identity_uses_part_index_not_shared_provider_id(first_event: str) -> None:
    observer = HarnessAguiObserver()
    identities = []
    for index in range(2):
        part = ThinkingPart(f"**Part {index}**", id="rs_shared", signature=f"signature-{index}")
        sources = []
        if first_event == "start":
            sources.append(PartStartEvent(index=index, part=ThinkingPart("", id=part.id)))
        if first_event != "end":
            sources.append(
                PartDeltaEvent(
                    index=index,
                    delta=ThinkingPartDelta(content_delta=part.content, signature_delta=part.signature),
                )
            )
        sources.append(PartEndEvent(index=index, part=part))
        events = [converted for source in sources for converted in observer.observe(_event(0, source))]
        starts = [event for event in events if isinstance(event, ReasoningMessageStartEvent)]
        assert len(starts) == 1
        message_id = starts[0].message_id
        identities.append(message_id)
        assert message_id == f"run-1:request-0:part-{index}:reasoning"
        assert [event.message_id for event in events if isinstance(event, ReasoningMessageContentEvent)] == [message_id]
        assert [event.entity_id for event in events if isinstance(event, ReasoningEncryptedValueEvent)] == [message_id]
        assert [event.message_id for event in events if isinstance(event, ReasoningMessageEndEvent)] == [message_id]
    assert len(set(identities)) == 2


def test_reasoning_end_keeps_open_identity_when_next_request_starts() -> None:
    observer = HarnessAguiObserver()
    part = ThinkingPart("first", id="rs_shared")
    started = observer.observe(_event(0, PartStartEvent(index=0, part=part)))[0]
    assert isinstance(started, ReasoningMessageStartEvent)
    observer.observe(
        _event(
            1, HarnessExtensionEvent(kind="lifecycle", payload={"type": "model_request_started", "request_index": 1})
        )
    )
    observer = HarnessAguiObserver.restore(observer.export())
    ended = observer.observe(_event(2, PartEndEvent(index=0, part=part)))[0]
    assert isinstance(ended, ReasoningMessageEndEvent)
    assert ended.message_id == started.message_id
    following = observer.observe(_event(3, PartStartEvent(index=0, part=part)))[0]
    assert isinstance(following, ReasoningMessageStartEvent)
    assert following.message_id != started.message_id


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


def test_native_tool_media_is_a_tool_result_not_user_input_or_binary_transport() -> None:
    from pydantic_ai.messages import BinaryContent

    image = BinaryContent(data=b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024), media_type="image/png")
    native = FunctionToolResultEvent(
        ToolReturnPart(tool_name="view", tool_call_id="call-image", content="The image/png file is attached."),
        content=[image],
    )
    assert native.event_kind == "function_tool_result"
    events = HarnessAguiObserver().observe(_event(0, native))
    assert len(events) == 1
    result = events[0]
    assert isinstance(result, ToolCallResultEvent)
    assert result.role == "tool"
    assert result.tool_call_id == "call-image"
    assert result.message_id == "call-image:result"
    assert result.content == "The image/png file is attached."
    assert len(result.model_dump_json()) < 512
    assert native.content == [image]  # Native model content is untouched.


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
    assert isinstance(events[0], RunStartedEvent)
    assert events[0].protocol_version == "1.0"
    assert sum(isinstance(event, RunStartedEvent) for event in events) == 1
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
        usage=RunUsageSummary(),
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
        usage=RunUsageSummary(input_tokens=2, output_tokens=3),
        failure=SafeFailure(code="model_failed", message="The model failed."),
    )
    cancelled = HarnessRunResult(
        thread_id=completed.thread_id,
        run_id="run-cancelled",
        status="cancelled",
        output=None,
        state=None,
        usage=RunUsageSummary(),
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
        usage=RunUsageSummary(),
    )
    opaque_event = HarnessAguiObserver().observe(_result_event(0, opaque))[0]

    assert isinstance(suspended_event, RunFinishedEvent)
    assert suspended_event.outcome.type == "interrupt"
    assert [(entry.id, entry.tool_call_id, entry.reason) for entry in suspended_event.outcome.interrupts] == [
        ("call-approval", "call-approval", "approval"),
        ("call-external", "call-external", "external"),
    ]
    assert isinstance(failed_event, RunErrorEvent)
    assert failed_event.code == "model_failed"
    assert failed_event.message == "The model failed."
    assert failed_event.usage is not None
    assert failed_event.usage[0].total_tokens == 5
    assert isinstance(cancelled_event, RunFinishedEvent)
    assert cancelled_event.outcome.type == "cancelled"
    assert isinstance(opaque_event, RunFinishedEvent)
    assert opaque_event.result is None
    assert opaque_event.raw_event["result_omitted"] is True
    TypeAdapter(Event).dump_json(opaque_event)


def test_input_projection_preserves_visibility_without_media_payloads() -> None:
    from a13n_harness.events import input_events
    from a13n_harness.model_context import user_prompt_content
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
    sources = input_events(user_prompt_content(prompt), source="user", input_id="input-one")
    observer = HarnessAguiObserver()
    events = tuple(event for index, source in enumerate(sources) for event in observer.observe(_event(index, source)))
    assert len(events) == 3
    bodies = [event.model_dump(mode="json") for event in events]
    for index, body in enumerate(bodies):
        assert body["value"]["event"]["role"] == "user"
        assert body["metadata"]["display"] is (index != 1)
    assert bodies[1]["metadata"]["private"] == "omit"
    assert bodies[1]["value"]["event"]["content"] == "private guidance"
    assert bodies[2]["name"] == "a13n.input.media"
    assert bodies[2]["value"]["event"]["content"] == {
        "kind": "binary",
        "media_type": "image/png",
        "size_bytes": 2 * 1024 * 1024,
        "payload_omitted": True,
    }
    assert len(TypeAdapter(list[Event]).dump_json(list(events))) < 4096
    assert observer.snapshot() == events

    def show_hidden(_source, event):
        if isinstance(event, CustomEvent):
            event.metadata["display"] = True
        return event

    with pytest.raises(AguiObservationError):
        HarnessAguiObserver(processor=show_hidden).observe(_event(0, sources[1]))


def test_input_image_links_are_presented_without_inline_data_or_fetching() -> None:
    from a13n_harness.events import input_events
    from a13n_harness.model_context import user_prompt_content
    from pydantic_ai.messages import ImageUrl, ModelMessagesTypeAdapter

    prompt = UserPromptPart(
        [
            ImageUrl("https://example.test/image.png"),
            ImageUrl("data:image/png;base64,cHJpdmF0ZS1ieXRlcw=="),
        ]
    )
    request = ModelRequest(parts=[prompt])
    before = ModelMessagesTypeAdapter.dump_json([request])
    observer = HarnessAguiObserver()
    events = tuple(
        event
        for index, source in enumerate(input_events(user_prompt_content(prompt), source="user", input_id="input-one"))
        for event in observer.observe(_event(index, source))
    )
    bodies = [event.model_dump(mode="json") for event in events]
    assert bodies[0]["value"]["event"]["content"]["url"] == "https://example.test/image.png"
    encoded = TypeAdapter(list[Event]).dump_json(list(events))
    assert b"base64" not in encoded and b"cHJpdmF0ZS1ieXRlcw==" not in encoded
    assert bodies[1]["value"]["event"]["content"]["payload_omitted"] is True
    assert ModelMessagesTypeAdapter.dump_json([request]) == before


@pytest.mark.parametrize("delivered", [False, True])
def test_native_input_types_preserve_caller_metadata_without_binary_transport(delivered: bool) -> None:
    from a13n_harness import ContentItem, ContentMetadata
    from a13n_harness.content import input_request
    from a13n_harness.events import input_events
    from pydantic_ai.messages import (
        AudioUrl,
        BinaryContent,
        CachePoint,
        DocumentUrl,
        ImageUrl,
        ModelMessagesTypeAdapter,
        TextContent,
        UploadedFile,
        VideoUrl,
    )

    metadata = {"image_object_id": "image-original", "client": {"selection": [1, 2]}}
    content = [
        "plain input",
        TextContent("annotated", metadata=metadata),
        ImageUrl("https://example.test/image.png"),
        AudioUrl("https://example.test/audio.mp3"),
        VideoUrl("https://example.test/video.mp4"),
        DocumentUrl("https://example.test/document.pdf"),
        BinaryContent(data=b"never-return-these-image-bytes", media_type="image/png"),
        UploadedFile("file-example", provider_name="openai"),
        CachePoint(),
    ]
    content = [
        ContentItem(item, ContentMetadata.model_validate(metadata))
        if not isinstance(item, str | TextContent | CachePoint)
        else item
        for item in content
    ]
    request = input_request(content)
    before = ModelMessagesTypeAdapter.dump_json([request])
    sources = input_events(content, source="steering" if delivered else "user", input_id="input-one")
    if delivered:
        sources.insert(0, EnqueuedMessagesEvent(enqueue_id="input-one", messages=(request,)))
    observer = HarnessAguiObserver()
    events = tuple(event for index, source in enumerate(sources) for event in observer.observe(_event(index, source)))
    bodies = [event.model_dump(mode="json") for event in events]
    media = [body for body in bodies if body.get("name") == "a13n.input.media"]
    assert [body["value"]["event"]["content"]["kind"] for body in media] == [
        "image-url",
        "audio-url",
        "video-url",
        "document-url",
        "binary",
        "uploaded-file",
    ]
    for body in media:
        assert body["metadata"]["image_object_id"] == "image-original"
        assert body["metadata"]["client"] == {"selection": [1, 2]}
    assert any(body.get("value", {}).get("event", {}).get("content") == "plain input" for body in bodies)
    annotated = next(body for body in bodies if body.get("value", {}).get("event", {}).get("content") == "annotated")
    assert annotated["metadata"]["image_object_id"] == "image-original"
    encoded = TypeAdapter(list[Event]).dump_json(list(events))
    import base64

    assert b"never-return-these-image-bytes" not in encoded
    assert base64.b64encode(content[6].value.data) not in encoded
    if delivered:
        delivery = next(body for body in bodies if body.get("name") == "a13n.pydantic_ai.enqueued_messages")
        assert delivery["value"]["event"] == {"event_kind": "enqueued_messages", "enqueue_id": "input-one"}
    assert ModelMessagesTypeAdapter.dump_json([request]) == before


@pytest.mark.parametrize("source", ["user", "steering", "context", "recovery", "async_subagent", "background_process"])
def test_input_sources_are_custom_text_events_and_cache_points_are_omitted(source) -> None:
    from a13n_harness.events import input_events
    from pydantic_ai.messages import CachePoint

    assert input_events([CachePoint()], source=source, input_id="input-one") == []
    native = input_events(["source text", CachePoint()], source=source, input_id="input-one")
    events = HarnessAguiObserver().observe(_event(0, native[0]))
    assert len(events) == 1 and isinstance(events[0], CustomEvent)
    assert events[0].name == f"a13n.input.{source}"
    assert events[0].value["event"] == {
        "input_id": "input-one",
        "source": source,
        "content": "source text",
        "message_id": "run-1:input:0",
        "role": "user" if source in {"user", "steering"} else "system",
    }
    assert not events[0].model_extra


def test_snapshot_ranges_are_detached_and_keep_a_fixed_boundary() -> None:
    observer = HarnessAguiObserver()
    observer.observe(_event(1, PartStartEvent(index=0, part=TextPart(content="begin"))))
    stop = observer.event_count
    original = observer.snapshot()
    observer.observe(_event(2, PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="later"))))
    assert observer.event_count > stop
    assert observer.snapshot(start=0, stop=stop) == original
    assert observer.snapshot(start=stop, stop=stop) == ()
    assert observer.snapshot(start=stop) == observer.snapshot()[stop:]
    for start, end in ((-1, stop), (1, 0), (0, observer.event_count + 1)):
        with pytest.raises(ValueError, match="snapshot range"):
            observer.snapshot(start=start, stop=end)
    content = next(event for event in original if isinstance(event, TextMessageContentEvent))
    content.delta = "mutated"
    assert all(
        event.delta != "mutated" for event in observer.snapshot(stop=stop) if isinstance(event, TextMessageContentEvent)
    )


def test_steering_request_start_before_previous_part_end_preserves_message_identity() -> None:
    observer = HarnessAguiObserver()
    first = observer.observe(_event(0, PartStartEvent(index=0, part=TextPart("first"))))
    observer.observe(
        _event(
            1,
            HarnessExtensionEvent(kind="lifecycle", payload={"type": "model_request_started", "request_index": 1}),
        )
    )
    ended = observer.observe(_event(2, PartEndEvent(index=0, part=TextPart("first"))))
    second = observer.observe(_event(3, PartStartEvent(index=0, part=TextPart("second"))))
    second_end = observer.observe(_event(4, PartEndEvent(index=0, part=TextPart("second"))))
    assert isinstance(first[0], TextMessageStartEvent)
    assert isinstance(second[0], TextMessageStartEvent)
    assert [type(event) for event in ended] == [TextMessageEndEvent]
    assert ended[0].message_id == first[0].message_id
    assert second_end[0].message_id == second[0].message_id
    assert first[0].message_id != second[0].message_id
    starts = [event.message_id for event in observer.snapshot() if isinstance(event, TextMessageStartEvent)]
    assert len(starts) == len(set(starts)) == 2


@pytest.mark.parametrize("outcome", ["failed", "denied", "interrupted"])
def test_non_success_lowered_tool_media_keeps_outcome_without_transporting_payload(outcome) -> None:
    from a13n_harness.tools._output import _render_tool_return
    from pydantic_ai import BinaryContent, ToolReturn

    image = BinaryContent(b"\xff\x00payload", media_type="image/png")
    rendered = _render_tool_return(ToolReturn({"error": "not completed"}, content=[image]))
    part = ToolReturnPart("view", rendered.return_value, "call-failed", metadata=rendered.metadata, outcome=outcome)
    native = FunctionToolResultEvent(part)
    (event,) = HarnessAguiObserver().observe(_event(0, native))
    assert isinstance(event, CustomEvent)
    assert event.value["event"]["part"]["outcome"] == outcome
    assert event.value["event"]["part"]["content"] == {"error": "not completed"}
    assert "payload" not in event.model_dump_json()
    assert part.content == [{"error": "not completed"}, image]


@dataclass
class _PublicValue:
    value: Annotated[int, PlainSerializer(lambda value: f"id-{value}", return_type=str, when_used="json")]


@dataclass
class _CallbackOutput:
    value: str
    callback: Annotated[Callable[[], None], PlainSerializer(lambda value: None, return_type=None, when_used="json")]


def _callback_output(value: str) -> _CallbackOutput:
    def callback() -> None:
        raise AssertionError("Projection must not invoke the callback")

    return _CallbackOutput(value, callback)


def _annotated_output(
    value: str,
) -> list[Annotated[int, PlainSerializer(lambda value: f"id-{value}", return_type=str)]]:
    return [int(value)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("output_function", "expected"),
    [(_callback_output, {"value": "7", "callback": None}), (_annotated_output, ["id-7"])],
)
async def test_terminal_projection_uses_the_run_output_contract(output_function, expected) -> None:
    async def response(messages, info):
        yield "7"

    executable = HarnessBuilder().build(
        AgentSpec(), output_type=TextOutput(output_function), model=FunctionModel(stream_function=response)
    )
    observer = HarnessAguiObserver()
    sources = []
    async with executable.stream("go", bindings=RunBindings.embedded()) as stream:
        async for item in stream:
            sources.append(item)
            observer.observe(item)
    terminal = observer.snapshot()[-1]
    assert isinstance(terminal, RunFinishedEvent)
    assert terminal.result == expected
    assert "result_omitted" not in terminal.raw_event
    assert stream.result is not None
    assert stream.result.output_json() == expected
    restored = HarnessAguiObserver()
    await restored.resume(_history(*sources))
    assert restored.snapshot() == observer.snapshot()


@pytest.mark.anyio
async def test_tool_result_projection_preserves_dataclass_serializer_and_media_omission() -> None:
    from pydantic_ai.capabilities import Capability

    native = {"values": [_PublicValue(7)], "raw": b"private bytes"}

    def values() -> Any:
        return native

    async def response(messages, info):
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield "done"
        else:
            yield {0: DeltaToolCall(name="values", json_args="{}", tool_call_id="call-values")}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=response),
        capabilities=(Capability(tools=[values], id="values"),),
    )
    observer = HarnessAguiObserver()
    async with executable.stream("go", bindings=RunBindings.embedded()) as stream:
        async for item in stream:
            observer.observe(item)
    results = [event for event in observer.snapshot() if isinstance(event, ToolCallResultEvent)]
    assert len(results) == 1
    assert json.loads(results[0].content) == {
        "values": [{"value": "id-7"}],
        "raw": {"payload_omitted": True, "size_bytes": 13},
    }
    assert native == {"values": [_PublicValue(7)], "raw": b"private bytes"}


@pytest.mark.parametrize("as_model", [False, True])
def test_structured_tool_media_is_omitted_before_custom_serialization(as_model: bool) -> None:
    from a13n_stream_protocol.content import tool_result_content
    from pydantic_ai.messages import BinaryContent

    calls = []

    @dataclass
    class Record:
        value: Any

        @model_serializer
        def serialize(self):
            calls.append(self)
            return {"flattened": "private payload"}

    class ModelRecord(BaseModel):
        value: Any

        @model_serializer
        def serialize(self):
            calls.append(self)
            return {"flattened": "private payload"}

    media = BinaryContent(data=b"private payload", media_type="image/png")
    record = ModelRecord(value={"nested": [media]}) if as_model else Record(value={"nested": [media]})
    content = tool_result_content({"record": record, "other": [_PublicValue(7)]})
    assert json.loads(content) == {"record": {"payload_omitted": True}, "other": [{"value": "id-7"}]}
    assert calls == []
    assert media.data == b"private payload"
