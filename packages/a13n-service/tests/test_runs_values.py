"""Run value contracts reject impossible outcomes and account for the bytes stored on disk."""

from datetime import UTC, datetime

import pytest
from a13n_service.runs.display import Display, DisplayFold, Item
from a13n_service.runs.schemas import Outcome, Pending, PendingItem
from pydantic import ValidationError

PENDING = Pending(items=(PendingItem(tool_call_id="call_test", kind="client_tool", tool_name="lookup", arguments={}),))
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
