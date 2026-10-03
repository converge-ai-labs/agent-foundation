"""Compact display and typed changes have one semantic owner for every Host."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from a13n_harness import HarnessEvent, HarnessExtensionEvent
from a13n_stream_protocol import AguiObservationError, HarnessAguiStreamObserver
from a13n_stream_protocol.display import AppendItem, DisplayFold, Item, Tail, apply_changes
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta


def source(sequence: int, event: Any, *, run: str = "root") -> HarnessEvent:
    return HarnessEvent(
        thread_id=f"thread-{run}",
        run_id=run,
        sequence=sequence,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        event=event,
    )


def fold() -> DisplayFold:
    return DisplayFold("run", Tail(), attempt=1, page_items=2, page_bytes=65536)


def test_live_conversion_keeps_no_raw_event_prefix() -> None:
    display = fold()
    item = source(1, PartStartEvent(index=0, part=TextPart(content="hello")))
    display.fold(display.events(item), item)
    for sequence in range(2, 1002):
        item = source(sequence, PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="!")))
        display.fold(display.events(item), item)
    assert display.observer.event_count == 0
    with pytest.raises(AguiObservationError, match="does not retain"):
        display.observer.snapshot()
    assert len(display.items) == 1
    assert next(iter(display.items.values())).content["text"] == "hello" + "!" * 1000


def test_typed_changes_reproduce_fold_without_source_event_interpretation() -> None:
    display, client = fold(), {}
    events = [
        {"type": "TEXT_MESSAGE_START", "messageId": "m", "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": "hello"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": " world"},
        {"type": "TEXT_MESSAGE_END", "messageId": "m"},
        {"type": "TOOL_CALL_START", "toolCallId": "t", "toolCallName": "work"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "t", "delta": "{}"},
        {"type": "TOOL_CALL_END", "toolCallId": "t"},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "t", "content": "done"},
    ]
    observed = display.fold(events)
    for event in observed:
        apply_changes(client, event.changes)
    assert client == display.items
    assert isinstance(observed[2].changes[0], AppendItem)
    assert observed[2].changes[0].text == " world"
    assert "hello" not in observed[2].changes[0].model_dump_json()
    assert observed[6].item.state == "in_progress"  # Arguments finished, tool not finished.


def test_snapshot_is_detached_from_later_argument_fragments() -> None:
    display = fold()

    def arguments(text: str) -> dict[str, Any]:
        return {
            "type": "CUSTOM",
            "name": "a13n.pydantic_ai.part_delta",
            "value": {
                "thread_id": "thread",
                "run_id": "root",
                "event": {
                    "index": 0,
                    "delta": {"part_delta_kind": "tool_call", "args_delta": text},
                },
            },
        }

    first = display.fold([arguments("a")])[0]
    saved = display.snapshot()
    serialized = saved.tail.model_dump_json()
    first_change = first.changes[0].model_dump_json()
    display.fold([arguments("b")])
    assert saved.tail.model_dump_json() == serialized
    assert first.changes[0].model_dump_json() == first_change
    assert next(iter(display.items.values())).content["value"]["event"]["delta"]["args_delta"] == "ab"


def test_closed_inline_conversion_state_is_released() -> None:
    observer = HarnessAguiStreamObserver(retain_events=False)
    for index in range(100):
        child = f"child-{index}"
        for action in ("started", "completed"):
            payload = {
                "type": "inline_delegation",
                "invocation_id": f"delegation-{index}",
                "action": action,
                "child_instance_id": child,
                "child_run_id": child,
                "subagent": "worker",
                "status": action,
                "parent_run_id": "root",
                "parent_agent_instance_id": "agent",
                "parent_tool_call_id": f"call-{index}",
            }
            observer.observe(
                source(
                    index * 3 + (1 if action == "started" else 3),
                    HarnessExtensionEvent(kind="delegation", payload=payload),
                )
            )
            if action == "started":
                observer.observe(
                    source(index * 3 + 2, PartStartEvent(index=0, part=TextPart(content="child")), run=child)
                )
        assert not observer._state.children
        assert observer._state.threads == {"root": "thread-root"}
    assert observer.event_count == 0


def test_baseline_plus_suffix_continues_open_tool_in_place() -> None:
    before = fold()
    before.fold(
        [
            {"type": "TOOL_CALL_START", "toolCallId": "t", "toolCallName": "work"},
            {"type": "TOOL_CALL_ARGS", "toolCallId": "t", "delta": "{}"},
        ]
    )
    baseline = before.snapshot({"t"}).tail
    client: dict[str, Item] = {item.id: item.model_copy(deep=True) for item in baseline.items}
    after = DisplayFold("run", baseline, attempt=2, page_items=2, page_bytes=65536)
    update = after.fold([{"type": "TOOL_CALL_RESULT", "toolCallId": "t", "content": "result"}])[0]
    apply_changes(client, update.changes)
    assert client == after.items
    assert list(client) == [baseline.items[0].id]
    assert next(iter(client.values())).state == "completed"


def test_cross_language_fixture_matches_the_semantic_owner() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "compact-display.json").read_text())
    display = DisplayFold("fixture", Tail(), attempt=1, page_items=128, page_bytes=262144)
    observed = display.fold(fixture["events"])
    assert [[change.model_dump(mode="json") for change in batch.changes] for batch in observed] == fixture["batches"]
    assert [item.model_dump(mode="json") for item in display.items.values()] == fixture["items"]
    client: dict[str, Item] = {}
    for batch in observed:
        apply_changes(client, batch.changes)
    assert client == display.items


@pytest.mark.parametrize("retain_complete", [False, True])
def test_host_retention_policy_preserves_complete_ui_output_and_edit_evidence(retain_complete: bool) -> None:
    from a13n_stream_protocol.display import MAX_FIELD_CHARS

    display = DisplayFold("run", Tail(), attempt=1, page_items=128, page_bytes=65536, retain_complete=retain_complete)
    text = "x" * (MAX_FIELD_CHARS + 100)
    display.fold(
        [
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": text},
            {"type": "TOOL_CALL_RESULT", "toolCallId": "t", "content": text},
            {
                "type": "CUSTOM",
                "name": "a13n.filesystem.edit_applied",
                "value": {"event": {"tool_call_id": "t", "before": text, "after": text + "changed"}},
            },
        ]
    )
    message, tool, *evidence = display.items.values()
    assert message.content["text"] == (text if retain_complete else text[:MAX_FIELD_CHARS])
    assert tool.content["result"] == (text if retain_complete else text[:MAX_FIELD_CHARS])
    if retain_complete:
        assert not evidence
        assert tool.content["applied_edit"]["before"] == text
        assert tool.content["applied_edit"]["after"] == text + "changed"
        assert "truncated" not in message.content
    else:
        assert evidence[0].content["value"] == {"omitted": True}
        assert message.content["truncated"] is True


def test_authored_media_and_inline_input_share_semantics_without_sharing_identity() -> None:
    from a13n_harness.events import InputMediaEvent, InputTextEvent

    display = fold()
    for index, event in enumerate(
        (
            InputTextEvent(input_id="input", source="steering", content="look here"),
            InputMediaEvent(input_id="input", source="steering", content={"kind": "image", "url": "image.png"}),
        ),
        1,
    ):
        native = source(index, event)
        payloads = display.events(native)
        display.fold(payloads, native)
        # The same source message ID in an inline lane is a different item.
        display.fold([{**payload, "subagentRunId": "child"} for payload in payloads])
    assert len(display.items) == 4
    text, child_text, media, child_media = display.items.values()
    assert text.id != child_text.id and media.id != child_media.id
    assert text.content["text"] == "look here"
    assert media.content["input_media"] == {"kind": "image", "url": "image.png"}
    assert all(item.content["input_source"] == "steering" for item in display.items.values())
    assert all(item.content["input_group"] == "input" for item in display.items.values())
