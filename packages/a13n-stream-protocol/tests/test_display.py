"""Compact snapshots preserve raw-event continuation at every semantic cut."""

from datetime import UTC, datetime

import pytest
from a13n_harness import HarnessEvent
from a13n_stream_protocol import AguiObservationError, HarnessAguiObserver
from a13n_stream_protocol.display import DisplayFold
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)


def test_snapshot_and_raw_suffix_reconstruct_text_and_tool_lifecycle() -> None:
    fold = DisplayFold("run", attempt=1)
    events = [
        {"type": "TEXT_MESSAGE_START", "messageId": "message", "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "message", "delta": "hello"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "message", "delta": " world"},
        {"type": "TEXT_MESSAGE_END", "messageId": "message"},
        {"type": "TOOL_CALL_START", "toolCallId": "call", "toolCallName": "search"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "call", "delta": "{}"},
        {"type": "TOOL_CALL_END", "toolCallId": "call"},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "call", "content": "found"},
    ]
    for event in events:
        restored = DisplayFold.restore(fold.export())
        event["timestamp"] = 1000
        batch = fold.fold([event])[0]
        restored.fold([event])
        assert restored.items == fold.items
        if event["type"] == "TOOL_CALL_END":
            assert batch.item is not None and batch.item.state == "in_progress"
        if event["type"] == "TEXT_MESSAGE_END":
            assert batch.item is not None and batch.item.state == "completed"


@pytest.mark.parametrize("legacy", [False, True])
def test_native_retry_result_remains_failed_across_snapshot_restore(legacy: bool) -> None:
    from pydantic_ai.messages import FunctionToolResultEvent, ToolReturnPart

    events = HarnessAguiObserver().observe(
        HarnessEvent(
            thread_id="thread",
            run_id="run",
            sequence=1,
            occurred_at=datetime.now(UTC),
            event=FunctionToolResultEvent(ToolReturnPart("lookup", "Invalid arguments", "call", outcome="retried")),
        )
    )
    raw = [event.model_dump(mode="json", by_alias=True) for event in events]
    if legacy:
        # Older persisted wire events retain their original retry-prompt shape.
        raw[-1]["value"]["event"]["part"]["part_kind"] = "retry-prompt"
        raw[-1]["value"]["event"]["part"].pop("outcome")
    fold = DisplayFold("run")
    fold.fold(raw)
    (item,) = fold.items.values()
    assert item.content["retry"] is True
    assert item.state == "failed"
    assert item.content["value"] == "Invalid arguments"
    assert item.content["outcome"] == (None if legacy else "retried")
    assert DisplayFold.restore(fold.export()).items == fold.items


def test_snapshot_is_detached_from_later_raw_events() -> None:
    fold = DisplayFold("run")
    fold.fold([{"type": "TEXT_MESSAGE_CONTENT", "messageId": "message", "delta": "a"}])
    frozen = fold.export()
    before = frozen.model_dump_json()
    fold.fold([{"type": "TEXT_MESSAGE_CONTENT", "messageId": "message", "delta": "b"}])
    assert frozen.model_dump_json() == before
    assert next(iter(fold.items.values())).content["text"] == "ab"


@pytest.mark.parametrize("chunks", [100, 1000])
def test_nonretaining_conversion_has_identical_output_without_journal(chunks: int) -> None:
    retained = HarnessAguiObserver()
    compact = HarnessAguiObserver(retain_events=False)
    fragment = "x" * (10000 // chunks)
    source = [PartStartEvent(index=0, part=TextPart(""))]
    source.extend(PartDeltaEvent(index=0, delta=TextPartDelta(fragment)) for _ in range(chunks))
    source.append(PartEndEvent(index=0, part=TextPart("x" * 10000)))
    for sequence, event in enumerate(source, 1):
        wrapped = HarnessEvent(
            thread_id="thread", run_id="run", sequence=sequence, occurred_at=datetime.now(UTC), event=event
        )
        assert compact.observe(wrapped) == retained.observe(wrapped)
    assert retained.event_count >= chunks
    assert compact.event_count == 0
    assert not compact._state.parts
    with pytest.raises(AguiObservationError, match="retain_events"):
        compact.snapshot()


def test_inline_scopes_do_not_collide() -> None:
    fold = DisplayFold("run")
    for scope in ("first", "second"):
        fold.fold([{"type": "TEXT_MESSAGE_CONTENT", "messageId": "same", "subagentRunId": scope, "delta": scope}])
    assert len(fold.items) == 2
    assert {item.content["text"] for item in fold.items.values()} == {"first", "second"}


@pytest.mark.parametrize("full_content", [False, True])
def test_every_event_cut_roundtrips_normalized_state(full_content: bool) -> None:
    from a13n_stream_protocol.display import DisplaySnapshot
    from a13n_stream_protocol.fragments import fragment_custom_event
    from ag_ui.core.events import CustomEvent

    events = [
        {
            "type": "CUSTOM",
            "name": "a13n.harness.lifecycle",
            "value": {"event": {"payload": {"type": "model_request_started"}}},
        },
        {"type": "TEXT_MESSAGE_START", "messageId": "m", "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": "hello"},
        {"type": "REASONING_MESSAGE_START", "messageId": "r", "subagentRunId": "child"},
        {"type": "REASONING_MESSAGE_CONTENT", "messageId": "r", "subagentRunId": "child", "delta": "think"},
        {"type": "TOOL_CALL_START", "toolCallId": "t", "toolCallName": "search"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "t", "delta": '{"query":'},
    ]
    events.extend(
        frame.model_dump(mode="json", by_alias=True)
        for frame in fragment_custom_event(CustomEvent(name="large", value={"data": "x" * 60000}), identity="fragment")
    )
    for text in ('{"a":', "1}"):
        events.append(
            {
                "type": "CUSTOM",
                "name": "a13n.pydantic_ai.part_delta",
                "value": {
                    "thread_id": "thread",
                    "run_id": "run",
                    "event": {"index": 1, "delta": {"part_delta_kind": "tool_call", "args_delta": text}},
                },
            }
        )
    events.extend(
        [
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": " world"},
            {"type": "TEXT_MESSAGE_END", "messageId": "m"},
            {"type": "REASONING_MESSAGE_END", "messageId": "r", "subagentRunId": "child"},
            {"type": "TOOL_CALL_ARGS", "toolCallId": "t", "delta": '"a"}'},
            {"type": "TOOL_CALL_END", "toolCallId": "t"},
            {"type": "TOOL_CALL_RESULT", "toolCallId": "t", "content": "found"},
        ]
    )
    for index, event in enumerate(events):
        event["timestamp"] = 1700000000000 + index
    uninterrupted = DisplayFold("run", attempt=2, full_content=full_content)
    uninterrupted.fold(events)
    expected = uninterrupted.export()
    for cut in range(len(events) + 1):
        fold = DisplayFold("run", attempt=2, full_content=full_content)
        fold.fold(events[:cut])
        frozen = fold.export()
        encoded = frozen.model_dump_json()
        restored = DisplayFold.restore(DisplaySnapshot.model_validate_json(encoded))
        assert fold.export() == frozen  # Export does not synthesize an END.
        restored.fold(events[cut:])
        assert restored.export() == expected, cut
        assert frozen.model_dump_json() == encoded  # No mutable aliases.


@pytest.mark.parametrize("reasoning", [False, True])
def test_native_converter_continues_after_each_serialized_cut(reasoning: bool) -> None:
    from a13n_stream_protocol.display import DisplaySnapshot

    sources = [
        PartStartEvent(index=0, part=TextPart("hello")),
        PartDeltaEvent(index=0, delta=TextPartDelta(" world")),
        PartEndEvent(index=0, part=TextPart("hello world")),
    ]
    if reasoning:
        sources = [
            PartStartEvent(index=0, part=ThinkingPart("**First", id="rs_shared")),
            PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=" plan**", signature_delta="signature-0")),
            PartEndEvent(index=0, part=ThinkingPart("**First plan**", id="rs_shared", signature="signature-0")),
            PartStartEvent(index=1, part=ThinkingPart("**Second", id="rs_shared")),
            PartDeltaEvent(index=1, delta=ThinkingPartDelta(content_delta=" plan**", signature_delta="signature-1")),
            PartEndEvent(index=1, part=ThinkingPart("**Second plan**", id="rs_shared", signature="signature-1")),
        ]
    wrapped = [
        HarnessEvent(
            thread_id="thread",
            run_id="run",
            sequence=index + 1,
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            event=event,
        )
        for index, event in enumerate(sources)
    ]
    expected = DisplayFold("run")
    for source in wrapped:
        expected.fold(expected.events(source), source)
    if reasoning:
        items = list(expected.items.values())
        assert [item.content["text"] for item in items] == ["**First plan**", "**Second plan**"]
        assert all(item.state == "completed" for item in items)
    for cut in range(len(wrapped) + 1):
        fold = DisplayFold("run")
        for source in wrapped[:cut]:
            fold.fold(fold.events(source), source)
        restored = DisplayFold.restore(DisplaySnapshot.model_validate_json(fold.export().model_dump_json()))
        for source in wrapped[cut:]:
            restored.fold(restored.events(source), source)
        assert restored.export() == expected.export()
        assert restored.observer.event_count == 0


def test_cross_language_normalization_fixture() -> None:
    """The same raw events and cuts are consumed by the TypeScript normalizer."""
    import json
    from pathlib import Path

    from a13n_stream_protocol.display import DisplayContinuation, DisplaySnapshot

    cases = json.loads((Path(__file__).parent / "fixtures/display-normalization.json").read_text())
    for case in cases:
        fold = DisplayFold.restore(
            DisplaySnapshot(items=[], continuation=DisplayContinuation.model_validate(case["initial"]))
        )
        for step in case["steps"]:
            observed = fold.fold([step["delta"]["event"]])[0]
            assert observed.model_dump(mode="json", exclude={"changes"}) == {
                key: value for key, value in step["delta"].items() if key not in {"run_id", "attempt"}
            }
            if observed.item is not None:
                assert fold.items[observed.item.id].model_dump(mode="json") == step["item"]
            assert fold.export().continuation.model_dump(mode="json") == step["continuation"]
            fold = DisplayFold.restore(DisplaySnapshot.model_validate_json(fold.export().model_dump_json()))


def test_host_processor_is_shared_by_live_display_and_restored_continuation() -> None:
    from ag_ui.core import CustomEvent, TextMessageContentEvent

    def process(source, event):
        if isinstance(event, TextMessageContentEvent):
            return event.model_copy(update={"delta": event.delta.upper()})
        if isinstance(event, CustomEvent):
            return None
        return event

    fold = DisplayFold("run", processor=process)
    sources = [
        PartStartEvent(index=0, part=TextPart("hello")),
        PartDeltaEvent(index=0, delta=TextPartDelta(" world")),
        PartEndEvent(index=0, part=TextPart("hello world")),
    ]
    live: list[dict] = []
    for sequence, event in enumerate(sources):
        source = HarnessEvent(
            thread_id="thread", run_id="run", sequence=sequence, occurred_at=datetime.now(UTC), event=event
        )
        restored = DisplayFold.restore(fold.export(), processor=process)
        converted = fold.events(source)
        assert restored.events(source) == converted
        live.extend(converted)
        fold.fold(converted, source)
        restored.fold(converted, source)
        assert restored.export() == fold.export()
    assert "".join(event["delta"] for event in live if event["type"] == "TEXT_MESSAGE_CONTENT") == "HELLO WORLD"
    assert next(item for item in fold.items.values() if item.kind == "text_message").content["text"] == "HELLO WORLD"
    assert not any(event["type"] == "CUSTOM" for event in live)


def _recall(memory: str, record: str, *, scope: str | None = None) -> dict:
    event = {
        "type": "CUSTOM",
        "name": "a13n.harness.context",
        "value": {
            "event": {
                "payload": {
                    "type": "memory_recall",
                    "memories": [
                        {"memory": memory, "recall": "recalled", "records": [{"id": record, "sha256": "a" * 64}]}
                    ],
                }
            }
        },
    }
    if scope:
        event["subagentRunId"] = scope
    return event


def test_memory_sources_survive_cuts_and_are_scoped_to_replies() -> None:
    from a13n_stream_protocol.display import DisplaySnapshot

    fold = DisplayFold("run", attempt=1)
    fold.fold([_recall("facts", "record-1")])
    frozen = fold.export().model_dump_json()
    fold = DisplayFold.restore(DisplaySnapshot.model_validate_json(frozen))
    for message, scope in (("answer", None), ("child-answer", "child")):
        events = [
            {"type": "TEXT_MESSAGE_START", "messageId": message, "role": "assistant"},
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": message, "delta": "hello"},
            {"type": "TEXT_MESSAGE_END", "messageId": message},
        ]
        if scope:
            for event in events:
                event["subagentRunId"] = scope
        for event in events:
            event["timestamp"] = 1000
            restored = DisplayFold.restore(fold.export())
            [observed] = fold.fold([event])
            restored.fold([event])
            assert restored.export() == fold.export()
            assert observed.item is not None
            item = fold.items[observed.item.id]
            if scope:
                assert "memory_sources" not in item.content
                assert observed.item.memory_sources is None
            elif event["type"] == "TEXT_MESSAGE_END":
                assert observed.item.model_dump(mode="json")["memory_sources"] == item.content["memory_sources"]
                assert item.content["memory_sources"][0]["records"][0]["id"] == "record-1"
            else:
                assert observed.item.memory_sources is None
    # Later deliveries do not rewrite already completed replies.
    saved = [item.model_dump_json() for item in fold.items.values() if item.kind == "text_message"]
    fold.fold([_recall("facts", "record-2")])
    assert saved == [item.model_dump_json() for item in fold.items.values() if item.kind == "text_message"]


def test_old_memory_observations_do_not_invent_empty_evidence() -> None:
    fold = DisplayFold("run")
    event = _recall("facts", "record")
    del event["value"]["event"]["payload"]["memories"][0]["records"]
    fold.fold(
        [
            event,
            {"type": "TEXT_MESSAGE_START", "messageId": "m", "role": "assistant"},
            {"type": "TEXT_MESSAGE_END", "messageId": "m"},
        ]
    )
    assert "memory_sources" not in list(fold.items.values())[-1].content


@pytest.mark.parametrize("status", [[], "future-status", "recalled"])
def test_unknown_context_payloads_remain_observations(status) -> None:
    event = _recall("facts", "record")
    memory = event["value"]["event"]["payload"]["memories"][0]
    memory["recall"] = status
    memory["records"] = ["future-format"]
    fold = DisplayFold("run")
    fold.fold([event])
    assert fold.memory_sources == {}
    assert next(iter(fold.items.values())).content["name"] == "a13n.harness.context"


def test_checkpoint_delta_is_detached_and_independent_of_live_drain():
    fold = DisplayFold("run")
    fold.fold([{"type": "TEXT_MESSAGE_CONTENT", "messageId": "first", "delta": "a"}])
    first = fold.capture_delta()
    frozen = first.model_dump_json()
    fold.changed.clear()
    fold.fold([{"type": "TEXT_MESSAGE_CONTENT", "messageId": "second", "delta": "b"}])
    fold.changed.clear()
    second = fold.capture_delta()
    assert len(second.items) == 1
    assert second.items[0].content["text"] == "b"
    fold.fold([{"type": "TEXT_MESSAGE_CONTENT", "messageId": "first", "delta": " correction"}])
    corrected = fold.capture_delta()
    assert len(corrected.items) == 1
    assert corrected.items[0].id == first.items[0].id
    assert corrected.items[0].content["text"] == "a correction"
    assert first.model_dump_json() == frozen
    corrected.items[0].content["text"] = "caller mutation"
    assert next(iter(fold.items.values())).content["text"] == "a correction"
    assert fold.capture_delta().items == []
