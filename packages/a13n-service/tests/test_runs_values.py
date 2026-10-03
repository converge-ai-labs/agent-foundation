"""Run value contracts reject impossible outcomes and account for the bytes stored on disk."""

from datetime import UTC, datetime

import pytest
from a13n_service.runs.schemas import Outcome, Pending, PendingCall
from a13n_stream_protocol.display import DisplayFold, Item, Tail
from pydantic import ValidationError

PENDING = Pending(approvals=(), calls=(PendingCall(tool_call_id="call_test", tool_name="lookup", arguments={}),))
FAILURE = {"code": "test_failure", "message": "Failed"}


def test_new_attempts_do_not_inherit_a_tail_resume_hint() -> None:
    tail = Tail.model_validate({"items": [], "position": {"attempt": 1, "sequence": 3}, "resume_after": "123-0"})
    assert Tail.model_validate_json(tail.model_dump_json()).resume_after == "123-0"
    fold = DisplayFold("run_test", tail, attempt=2, page_items=256, page_bytes=1048576)
    assert fold.snapshot().tail.resume_after is None


@pytest.mark.parametrize(
    "value",
    [
        {"status": "waiting"},
        {"status": "completed", "pending": PENDING},
        {"status": "failed"},
        {"status": "cancelled"},
        {"status": "completed", "failure": FAILURE},
        {"status": "waiting", "pending": PENDING, "failure": FAILURE},
        {"status": "failed", "failure": FAILURE, "output": "uncommitted"},
    ],
)
def test_outcomes_reject_contradictory_fields(value: dict) -> None:
    with pytest.raises(ValidationError):
        Outcome.model_validate(value)


@pytest.mark.parametrize(
    "outcome",
    [
        Outcome(status="completed"),
        Outcome(status="completed", output={"answer": 42}),
        Outcome(status="waiting", pending=PENDING),
        Outcome.failed("test", "x" * 5000),
        Outcome.cancelled(),
    ],
)
def test_valid_outcomes_round_trip(outcome: Outcome) -> None:
    assert Outcome.model_validate_json(outcome.model_dump_json()) == outcome


@pytest.mark.parametrize("text", ["汉" * 30000, "🙂" * 20000])
def test_page_bytes_count_utf8_bytes(text: str) -> None:
    item = Item(
        id="itm_test",
        ordinal=1,
        kind="text_message",
        state="completed",
        first_stream_id="1-1",
        last_stream_id="1-2",
        started_at=datetime(2026, 9, 26, tzinfo=UTC),
        content={"text": text},
    )
    snapshot = DisplayFold("run_test", Tail(items=[item]), attempt=1, page_items=256, page_bytes=65536).snapshot()
    assert [page.items for page in snapshot.pages] == [[item]]
    assert snapshot.tail.first == 2 and snapshot.tail.items == []


@pytest.mark.parametrize(
    "value",
    [
        {"approvals": [], "calls": []},
        {"approvals": PENDING.calls, "calls": PENDING.calls},
        {"approvals": [], "calls": PENDING.calls * 129},
    ],
)
def test_pending_rejects_empty_duplicate_or_oversized_batches(value):
    with pytest.raises(ValidationError):
        Pending.model_validate(value)


@pytest.mark.parametrize(
    "value",
    [
        {"approvals": {}, "calls": {"c": {"status": "failed", "message": "  "}}},
        {"approvals": {"c": {"action": "approve"}}, "calls": {"c": {"status": "returned", "value": None}}},
        {"approvals": {}, "calls": {"": {"status": "returned", "value": None}}},
        {"approvals": {}, "calls": {str(i): {"status": "returned", "value": None} for i in range(129)}},
        {"approvals": {}, "calls": {"c": {"status": "returned", "value": "字" * 100000}}},
    ],
)
def test_resume_rejects_invalid_envelopes_and_limits(value):
    from a13n_service.runs.schemas import Resume

    with pytest.raises(ValidationError):
        Resume.model_validate(value)


def test_public_pending_preserves_approval_details_without_exposing_call_metadata():
    from a13n_harness.tools.approval import APPROVAL_PRESENTATION_KEY
    from a13n_service.runs.deferred import pending
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import DeferredToolRequests

    details = {"target": "report.txt", "risk": "high"}
    native = DeferredToolRequests(
        approvals=[ToolCallPart("delete_file", {"path": "report.txt"}, "approval")],
        calls=[ToolCallPart("review_invoice", {"invoice": 7}, "review")],
        metadata={
            "approval": {APPROVAL_PRESENTATION_KEY: details, "internal": "private approval"},
            "review": {
                APPROVAL_PRESENTATION_KEY: details,
                "a13n.harness.deferred-function-id": "tool/private/review",
                "internal": {"retained": True},
            },
        },
    )
    public = pending(native)
    assert public.approvals[0].presentation == details
    assert public.calls[0].presentation is None
    assert public.calls[0].arguments == {"invoice": 7}
    assert "internal" not in public.model_dump_json() and "tool/private" not in public.model_dump_json()
    assert native.metadata["review"]["internal"] == {"retained": True}


@pytest.mark.parametrize("source", ["user", "steering", "context", "recovery", "async_subagent", "background_process"])
@pytest.mark.parametrize("length", [20, 80000, 300000])
def test_source_typed_input_folds_to_authored_message_or_generated_observation(source, length):
    from a13n_harness import HarnessEvent
    from a13n_harness.content import ContentMetadata
    from a13n_harness.events import InputTextEvent

    text = "x" * length
    fold = DisplayFold("run_test", Tail(), attempt=1, page_items=256, page_bytes=1048576)
    event = HarnessEvent(
        thread_id="thread_test",
        run_id="run_test",
        sequence=1,
        occurred_at=datetime.now(UTC),
        event=InputTextEvent(
            input_id="input_one",
            source=source,
            content=text,
            metadata=ContentMetadata(source_id="inbox_one", display=source in {"user", "steering"}),
        ),
    )
    payloads = fold.events(event)
    observed = fold.fold(payloads)
    if length > 48000:
        assert all(item.item is None for item in observed[:-1])
    display = fold.snapshot().tail
    assert len(display.items) == 1
    item = display.items[0]
    assert item.state == "completed"
    assert item.last_stream_id == f"1-{len(payloads)}"
    if source in {"user", "steering"}:
        assert item.kind == "text_message"
        assert item.content["role"] == "user"
        assert item.content["text"] == text[:262144]
        assert item.content["metadata"]["source_id"] == "inbox_one"
        assert item.content.get("truncated", False) is (length > 262144)
    else:
        assert item.kind == "observation"
        assert item.content["name"] == f"a13n.input.{source}"
    assert Tail.model_validate_json(display.model_dump_json()) == display


def _message(number: int, text: str = "") -> list[dict]:
    message_id = f"msg_{number}"
    return [
        {"type": "TEXT_MESSAGE_START", "messageId": message_id, "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": message_id, "delta": text or str(number)},
        {"type": "TEXT_MESSAGE_END", "messageId": message_id},
    ]


def test_snapshots_page_final_items_and_keep_only_open_tool_calls_unfinished():
    fold = DisplayFold("run_test", Tail(), attempt=1, page_items=16, page_bytes=1048576)
    # An inline child failed after starting a call: nothing can finish it, so it is final once interrupted.
    fold.fold([{"type": "TOOL_CALL_START", "toolCallId": "call_child", "toolCallName": "find", "subagentRunId": "c"}])
    for number in range(20):
        fold.fold(_message(number))
    fold.fold([{"type": "TOOL_CALL_START", "toolCallId": "call_open", "toolCallName": "find"}])
    fold.fold(_message(20))

    snapshot = fold.snapshot(open_calls={"call_open"})

    (page,) = snapshot.pages
    assert [item.ordinal for item in page.items] == list(range(1, 17))
    assert page.items[0].kind == "tool_call" and page.items[0].state == "interrupted"
    # The open call holds every later item in the tail.
    assert snapshot.tail.first == 17 and [item.ordinal for item in snapshot.tail.items] == list(range(17, 24))
    assert [item.state for item in snapshot.tail.items if item.kind == "tool_call"] == ["in_progress"]
    fold.committed(snapshot)
    with pytest.raises(RuntimeError):
        fold.fold(_message(0))

    restored = DisplayFold(
        "run_test",
        Tail.model_validate_json(snapshot.tail.model_dump_json()),
        attempt=2,
        page_items=16,
        page_bytes=1048576,
    )
    restored.fold([{"type": "TOOL_CALL_RESULT", "toolCallId": "call_open", "messageId": "result", "content": "ok"}])
    for number in range(21, 30):
        restored.fold(_message(number))
    (page,) = restored.snapshot().pages
    assert [item.ordinal for item in page.items] == list(range(17, 33))


def test_a_page_ends_at_its_byte_limit():
    fold = DisplayFold("run_test", Tail(), attempt=1, page_items=256, page_bytes=65536)
    for number in range(5):
        fold.fold(_message(number, "x" * 40000))
    snapshot = fold.snapshot()
    assert [[item.ordinal for item in page.items] for page in snapshot.pages] == [[1, 2], [3, 4]]
    assert [item.ordinal for item in snapshot.tail.items] == [5]
