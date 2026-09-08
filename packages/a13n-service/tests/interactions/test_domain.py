from __future__ import annotations

from datetime import UTC, datetime

import pytest
from a13n_service.interactions.domain import (
    PendingCallKind,
    PendingCallSummary,
    RunPendingSummary,
    RunUsage,
    RunUsageLimit,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)
from a13n_service.interactions.state import (
    CompletedOutcomeCandidate,
    DeferredContinuationState,
    HostContinuationState,
    InboxReceipt,
    WaitingOutcomeCandidate,
    validate_state_successor,
)
from pydantic import ValidationError
from pydantic_ai.usage import UsageLimits

from .conftest import ATTEMPT_ID, initial_state, progress_state

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def test_thread_origin_combinations_are_explicit() -> None:
    root = Thread(
        id="thread-1234567890abcdef1234567890abcdef",
        version=1,
        queue_version=0,
        organization_id="org_1234567890abcdef",
        session_id="sess_1234567890abcdef",
        role=ThreadRole.root,
        origin_kind=ThreadOriginKind.new,
        current_run_id="run_1234567890abcdef",
        created_at=NOW,
        updated_at=NOW,
    )
    assert root.origin_thread_id is None

    with pytest.raises(ValidationError, match="source references"):
        Thread(
            id="thread-2234567890abcdef1234567890abcdef",
            version=1,
            queue_version=0,
            organization_id="org_1234567890abcdef",
            session_id="sess_1234567890abcdef",
            role=ThreadRole.root,
            origin_kind=ThreadOriginKind.fork,
            current_run_id="run_2234567890abcdef",
            created_at=NOW,
            updated_at=NOW,
        )


def test_recovery_usage_addition_and_limits_include_named_units() -> None:
    charged = RunUsage(model_requests=1, input_tokens=10, billable_units={"gpu_seconds": 4})
    current = RunUsage(model_requests=2, output_tokens=5, billable_units={"gpu_seconds": 3})
    total = charged.plus(current)

    assert total == RunUsage(
        model_requests=3,
        input_tokens=10,
        output_tokens=5,
        billable_units={"gpu_seconds": 7},
    )
    assert RunUsageLimit(model_requests=3, billable_units={"gpu_seconds": 7}).permits(total)
    assert not RunUsageLimit(model_requests=2).permits(total)
    assert not RunUsageLimit(billable_units={"gpu_seconds": 6}).permits(total)


def test_state_successor_preserves_run_identity_and_increments_once() -> None:
    initial = initial_state()
    progress = progress_state(initial)

    validate_state_successor(initial, progress, run_attempt_id=ATTEMPT_ID, attempt_number=1)

    payload = progress.model_dump(mode="python")
    payload["checkpoint_seq"] = 3
    skipped = type(progress).model_validate(payload)
    with pytest.raises(ValueError, match="exactly one"):
        validate_state_successor(progress, skipped, run_attempt_id=ATTEMPT_ID, attempt_number=1)


def test_non_initial_checkpoint_requires_applied_input_and_attempt_fence() -> None:
    initial = initial_state()
    payload = initial.model_dump(mode="python")
    payload.update(checkpoint_seq=1, checkpoint_kind="progress")

    with pytest.raises(ValidationError, match="applied input"):
        type(initial).model_validate(payload)


def test_outcome_candidate_cannot_be_replaced_before_relational_sealing() -> None:
    initial = initial_state()
    payload = progress_state(initial).model_dump(mode="python")
    payload.update(
        checkpoint_kind="completed",
        outcome_candidate=CompletedOutcomeCandidate(output={"answer": 42}),
    )
    completed = type(initial).model_validate(payload)
    successor = progress_state(completed)

    with pytest.raises(ValueError, match="Run outcome candidate permits receipt repair"):
        validate_state_successor(completed, successor, run_attempt_id=ATTEMPT_ID, attempt_number=1)


def test_waiting_summary_must_match_native_deferred_request_kinds() -> None:
    initial = initial_state()
    payload = initial.model_dump(mode="python")
    payload.update(
        checkpoint_seq=1,
        checkpoint_kind="waiting",
        last_checkpoint_run_attempt_id=ATTEMPT_ID,
        last_checkpoint_fence=1,
        host=HostContinuationState(
            deferred=DeferredContinuationState(
                requests={
                    "calls": [],
                    "approvals": [
                        {
                            "tool_name": "dangerous_tool",
                            "args": {},
                            "tool_call_id": "approval-1",
                        }
                    ],
                    "metadata": {},
                }
            )
        ),
        outcome_candidate=WaitingOutcomeCandidate(
            wait_reason="client_tool",
            pending=RunPendingSummary(
                calls=(
                    PendingCallSummary(
                        call_id="approval-1",
                        kind=PendingCallKind.client_tool,
                        tool_name="dangerous_tool",
                    ),
                )
            ),
        ),
    )

    with pytest.raises(ValidationError, match="preserve native request kind"):
        type(initial).model_validate(payload)


@pytest.mark.parametrize("changed", [None, "outcome", "history"])
def test_terminal_receipt_repair_cannot_change_execution_or_result(changed):
    completed = progress_state(initial_state()).model_copy(
        update={"checkpoint_kind": "completed", "outcome_candidate": CompletedOutcomeCandidate(output="original")}
    )
    host = HostContinuationState(inbox_receipts=(InboxReceipt(inbox_entry_id="inb_7777777777777777", kind="steer"),))
    successor = completed.model_copy(
        update={
            "checkpoint_seq": completed.checkpoint_seq + 1,
            "host": host,
            "last_checkpoint_fence": 2,
        }
    )
    if changed == "outcome":
        successor = successor.model_copy(update={"outcome_candidate": CompletedOutcomeCandidate(output="changed")})
    elif changed == "history":
        successor = successor.model_copy(
            update={"harness": completed.harness.model_copy(update={"agent_context_state": {"changed": True}})}
        )
    if changed is None:
        validate_state_successor(completed, successor, run_attempt_id=ATTEMPT_ID, attempt_number=2)
    else:
        with pytest.raises(ValueError, match="permits receipt repair"):
            validate_state_successor(completed, successor, run_attempt_id=ATTEMPT_ID, attempt_number=2)


@pytest.mark.parametrize("limits", [None, UsageLimits(request_limit=4), UsageLimits(request_limit=1)])
def test_checkpoint_cannot_change_accepted_usage_limits(limits: UsageLimits | None) -> None:
    initial = initial_state().model_copy(update={"usage_limits": UsageLimits(request_limit=3)})
    successor = progress_state(initial).model_copy(update={"usage_limits": limits})
    with pytest.raises(ValueError, match="usage_limits"):
        validate_state_successor(initial, successor, run_attempt_id=ATTEMPT_ID, attempt_number=1)


def test_completed_candidate_continues_only_with_new_input_receipts():
    from a13n_service.interactions.state import CompletedOutcomeCandidate, HostContinuationState, InboxReceipt

    initial = initial_state()
    prior_receipt = InboxReceipt(inbox_entry_id="inb_1111111111111111", kind="steer")
    new_receipt = InboxReceipt(inbox_entry_id="inb_2222222222222222", kind="steer")
    completed = progress_state(initial).model_copy(
        update={
            "checkpoint_kind": "completed",
            "outcome_candidate": CompletedOutcomeCandidate(output="prior answer"),
            "host": HostContinuationState(inbox_receipts=(prior_receipt,)),
        }
    )
    continuation = progress_state(completed).model_copy(
        update={"host": HostContinuationState(inbox_receipts=(prior_receipt, new_receipt))}
    )
    validate_state_successor(completed, continuation, run_attempt_id=ATTEMPT_ID, attempt_number=1)
    assert not initial.initial_input_applied
    assert completed.initial_input_applied and continuation.initial_input_applied
    assert "initial_input_applied" not in continuation.model_dump()
    for receipts in ((prior_receipt,), (new_receipt,)):
        with pytest.raises(ValueError):
            validate_state_successor(
                completed,
                continuation.model_copy(update={"host": HostContinuationState(inbox_receipts=receipts)}),
                run_attempt_id=ATTEMPT_ID,
                attempt_number=1,
            )


def test_previous_checkpoint_schema_is_rejected_without_reinterpretation():
    from a13n_service.interactions.state import RunCheckpoint

    legacy = initial_state().model_dump(mode="python")
    legacy["schema_version"] = "1"
    legacy["input_disposition"] = "pending"
    legacy["host"] = {"schema_version": "1", "deferred": None, "consumed_inbox_entries": ()}
    with pytest.raises(ValidationError):
        RunCheckpoint.model_validate(legacy)
