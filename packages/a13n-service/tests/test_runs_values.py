"""Run value contracts reject impossible outcomes and account for the bytes stored on disk."""

from datetime import UTC, datetime

import pytest
from a13n_service.runs.display import Display, DisplayFold, Item
from a13n_service.runs.schemas import Outcome, Pending, PendingCall
from pydantic import ValidationError

PENDING = Pending(approvals=(), calls=(PendingCall(tool_call_id="call_test", tool_name="lookup", arguments={}),))
FAILURE = {"code": "test_failure", "message": "Failed"}


def test_old_displays_have_no_resume_hint_and_new_attempts_do_not_inherit_it() -> None:
    old = Display.model_validate({"items": [], "position": {"attempt": 1, "sequence": 3}, "dropped": 0})
    assert old.resume_after is None
    old.resume_after = "123-0"
    assert Display.model_validate_json(old.model_dump_json()).resume_after == "123-0"
    assert DisplayFold("run_test", old, attempt=2, max_bytes=65536).snapshot().resume_after is None


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
def test_display_budget_counts_utf8_bytes_and_caches_the_omitted_size(text: str) -> None:
    item = Item(
        id="itm_test",
        kind="text_message",
        state="completed",
        first_stream_id="1-1",
        last_stream_id="1-2",
        started_at=datetime(2026, 9, 26, tzinfo=UTC),
        content={"text": text},
    )
    fold = DisplayFold("run_test", Display(items=[item]), attempt=1, max_bytes=65536)
    snapshot = fold.snapshot()
    assert snapshot.items[0].content == {"omitted": True}
    assert len(snapshot.model_dump_json().encode()) < 65536
    assert fold.snapshot() == snapshot


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
    fold = DisplayFold("run_test", Display(), attempt=1, max_bytes=2 * 1024 * 1024)
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
    display = fold.snapshot()
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
    assert Display.model_validate_json(display.model_dump_json()) == display


def test_small_display_budget_omits_long_multibyte_input_content_not_the_message_item():
    from a13n_harness import HarnessEvent, InputTextEvent

    fold = DisplayFold("run_test", Display(), attempt=1, max_bytes=65536)
    source = HarnessEvent(
        thread_id="thread_test",
        run_id="run_test",
        sequence=1,
        occurred_at=datetime.now(UTC),
        event=InputTextEvent(input_id="input_one", source="user", content="汉" * 30000),
    )
    events = fold.events(source)
    assert len(events) > 1
    observed = fold.fold(events)
    assert observed[-1].item is not None
    assert observed[-1].item.kind == "text_message"
    assert not fold.assembler.gap
    snapshot = fold.snapshot()
    assert len(snapshot.items) == 1
    assert snapshot.items[0].content == {"omitted": True}
    assert snapshot.items[0].state == "completed"
    assert snapshot.items[0].last_stream_id == f"1-{len(events)}"
    assert len(snapshot.model_dump_json().encode()) <= 65536
