"""Shared item changes reconstruct the fold without replaying source semantics."""

from datetime import UTC, datetime

import pytest
from a13n_harness import HarnessEvent
from a13n_stream_protocol import AguiObservationError, HarnessAguiObserver
from a13n_stream_protocol.display import AppendItem, DisplayFold, Item, SetItem, apply_changes
from pydantic_ai.messages import PartDeltaEvent, PartEndEvent, PartStartEvent, TextPart, TextPartDelta


def test_changes_reconstruct_text_and_tool_lifecycle() -> None:
    fold = DisplayFold("run", attempt=1)
    reconstructed: dict[str, Item] = {}
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
        batch = fold.fold([event])[0]
        apply_changes(reconstructed, batch.changes)
        assert reconstructed == fold.items
        if event["type"] == "TOOL_CALL_END":
            assert batch.item is not None and batch.item.state == "in_progress"
        if event["type"] == "TEXT_MESSAGE_END":
            assert isinstance(batch.changes[0], SetItem)


def test_changes_are_atomic_on_missing_or_stale_predecessor() -> None:
    fold = DisplayFold("run")
    fold.fold([{"type": "TEXT_MESSAGE_CONTENT", "messageId": "message", "delta": "a"}])
    initial = next(iter(fold.items.values())).model_copy(deep=True)
    change = fold.fold([{"type": "TEXT_MESSAGE_CONTENT", "messageId": "message", "delta": "b"}])[0].changes[0]
    assert isinstance(change, AppendItem)
    items = {initial.id: initial}
    with pytest.raises(ValueError, match="predecessor"):
        apply_changes(items, [change, change.model_copy(update={"id": "missing"})])
    assert items == {initial.id: initial}
    apply_changes(items, [change])
    with pytest.raises(ValueError, match="predecessor"):
        apply_changes(items, [change])
    assert items == fold.items


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
