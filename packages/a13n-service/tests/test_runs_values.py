"""Run value contracts reject impossible outcomes and account for the bytes stored on disk."""

import pytest
from a13n_service.runs.display import Display
from a13n_service.runs.schemas import Outcome, Pending, PendingCall
from a13n_stream_protocol import DisplayProjector, DisplayScope
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart

PENDING = Pending(approvals=(), calls=(PendingCall(tool_call_id="call_test", tool_name="lookup", arguments={}),))
FAILURE = {"code": "test_failure", "message": "Failed"}


def test_display_cutover_rejects_old_values_and_attempts_start_without_a_hint() -> None:
    with pytest.raises(ValidationError):
        Display.model_validate({"items": [], "position": {"attempt": 1, "sequence": 3}, "dropped": 0})
    display = Display.empty("run_test", attempt=1).model_copy(update={"resume_after": "123-0"})
    assert Display.model_validate_json(display.model_dump_json()).resume_after == "123-0"
    renewed = Display(snapshot=display.for_attempt(2))
    assert renewed.resume_after is None
    assert renewed.position.attempt == 2 and renewed.position.sequence == 0


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
def test_display_budget_counts_utf8_bytes_and_retains_omission_count(text: str) -> None:
    projector = DisplayProjector(Display.empty("run_test", attempt=1).snapshot, max_bytes=65536)
    projector.scope(DisplayScope(id="scope", thread_id="thread", run_id="native"))
    projector.reconcile_message("scope", 0, ModelResponse(parts=[TextPart(text)]))
    snapshot = projector.capture()
    assert snapshot.blocks == () and snapshot.omitted == 1
    assert len(snapshot.model_dump_json().encode()) < 65536
    assert projector.capture() == snapshot


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
def test_canonical_input_visibility_and_source_observation_do_not_duplicate_display(source, length):
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent
    from a13n_harness.content import ContentItem, ContentMetadata, input_request
    from a13n_harness.events import InputTextEvent
    from a13n_service.runs.display import MAX_FIELD_CHARS
    from a13n_stream_protocol import HarnessAguiConverter

    text = "x" * length
    metadata = ContentMetadata(source_id="inbox_one", display=source in {"user", "steering"})
    projector = DisplayProjector(
        Display.empty("run_test", attempt=1).snapshot,
        max_bytes=2 * 1024 * 1024,
        max_field_chars=MAX_FIELD_CHARS,
    )
    projector.scope(DisplayScope(id="run_test", thread_id="thread_test", run_id="run_test"))
    projector.reconcile_message("run_test", 0, input_request([ContentItem(text, metadata)]))
    baseline = projector.capture()
    event = HarnessEvent(
        thread_id="thread_test",
        run_id="run_test",
        sequence=1,
        occurred_at=datetime.now(UTC),
        event=InputTextEvent(input_id="input_one", source=source, content=text, metadata=metadata),
    )
    # Explicit AG-UI observation remains source-typed and payload-fragmented.
    observed = HarnessAguiConverter().observe(event)
    assert observed
    # Compact display only reconciles the canonical annotated request, never a
    # second copy from the observation channel (including hidden generated input).
    projector.observe("run_test", 0, event.event)
    assert projector.capture() == baseline
    if source in {"user", "steering"}:
        (block,) = baseline.blocks
        assert block.kind == "input" and block.status == "succeeded"
        assert block.content["text"] == text[:MAX_FIELD_CHARS]
        assert block.content["metadata"]["source_id"] == "inbox_one"
        assert block.content.get("truncated", False) is (length > MAX_FIELD_CHARS)
    else:
        assert baseline.blocks == ()
        assert text not in baseline.model_dump_json()
    display = Display(snapshot=baseline)
    assert Display.model_validate_json(display.model_dump_json()) == display


def test_small_display_budget_evicts_multibyte_input_with_explicit_omission_coverage():
    from a13n_harness.content import input_request
    from a13n_stream_protocol import DisplayState

    baseline = Display.empty("run_test", attempt=1).snapshot
    receiver = DisplayState(baseline)
    projector = DisplayProjector(baseline, publish=receiver.apply, max_bytes=65536)
    projector.scope(DisplayScope(id="run_test", thread_id="thread_test", run_id="run_test"))
    projector.reconcile_message("run_test", 0, input_request("汉" * 30000))
    snapshot = projector.capture()
    assert snapshot.blocks == () and snapshot.omitted == 1
    assert snapshot.position.sequence > 0
    assert receiver.capture() == snapshot
    assert len(snapshot.model_dump_json().encode()) <= 65536
