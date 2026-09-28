"""Run value contracts reject impossible outcomes and account for the bytes stored on disk."""

from datetime import UTC, datetime

import pytest
from a13n_service.runs.display import Display, DisplayFold, Item
from a13n_service.runs.schemas import Outcome, Pending, PendingCall
from pydantic import ValidationError

PENDING = Pending(approvals=(), calls=(PendingCall(tool_call_id="call_test", tool_name="lookup", arguments={}),))
FAILURE = {"code": "test_failure", "message": "Failed"}


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
    "approvals,calls,reason", [(True, False, "approval"), (False, True, "call"), (True, True, "multiple")]
)
def test_wait_reason_summarizes_categories(approvals, calls, reason):
    approval = {"tool_call_id": "approval", "tool_name": "delete", "arguments": {}}
    # Question and custom human tools belong to the same calls category.
    questions = [
        {"tool_call_id": "question", "tool_name": "ask_user_question", "arguments": {}},
        {"tool_call_id": "review", "tool_name": "review_invoice", "arguments": {}},
    ]
    assert (
        Pending.model_validate(
            {"approvals": [approval] if approvals else [], "calls": questions if calls else []}
        ).reason
        == reason
    )


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


def test_public_pending_preserves_only_opted_in_presentation():
    from a13n_harness.tools.deferred import DEFERRED_PRESENTATION_KEY
    from a13n_service.runs.deferred import pending
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import DeferredToolRequests

    native = DeferredToolRequests(
        calls=[ToolCallPart("review_invoice", {"invoice": 7}, "review")],
        metadata={
            "review": {
                DEFERRED_PRESENTATION_KEY: {"title": "Review invoice"},
                "a13n.harness.deferred-function-id": "tool/private/review",
                "internal": {"retained": True},
            }
        },
    )
    public = pending(native)
    assert public.calls[0].presentation == {"title": "Review invoice"}
    assert "internal" not in public.model_dump_json() and "tool/private" not in public.model_dump_json()
    assert native.metadata["review"]["internal"] == {"retained": True}
