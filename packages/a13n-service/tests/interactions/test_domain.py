from __future__ import annotations

from datetime import UTC, datetime

import pytest
from a13n_service.interactions.domain import (
    PendingCallKind,
    PendingCallSummary,
    RecoveryUsage,
    RecoveryUsageLimit,
    RunPendingSummary,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)
from a13n_service.interactions.state import (
    CompletedOutcomeCandidate,
    DeferredContinuationState,
    HostContinuationState,
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
    charged = RecoveryUsage(model_requests=1, input_tokens=10, billable_units={"gpu_seconds": 4})
    current = RecoveryUsage(model_requests=2, output_tokens=5, billable_units={"gpu_seconds": 3})
    total = charged.plus(current)

    assert total == RecoveryUsage(
        model_requests=3,
        input_tokens=10,
        output_tokens=5,
        billable_units={"gpu_seconds": 7},
    )
    assert RecoveryUsageLimit(model_requests=3, billable_units={"gpu_seconds": 7}).permits(total)
    assert not RecoveryUsageLimit(model_requests=2).permits(total)
    assert not RecoveryUsageLimit(billable_units={"gpu_seconds": 6}).permits(total)


def test_state_successor_preserves_run_identity_and_increments_once() -> None:
    initial = initial_state()
    progress = progress_state(initial)

    validate_state_successor(initial, progress, run_attempt_id=ATTEMPT_ID, fence=1)

    payload = progress.model_dump(mode="python")
    payload["checkpoint_seq"] = 3
    skipped = type(progress).model_validate(payload)
    with pytest.raises(ValueError, match="exactly one"):
        validate_state_successor(progress, skipped, run_attempt_id=ATTEMPT_ID, fence=1)


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

    with pytest.raises(ValueError, match="outcome candidate state cannot be replaced"):
        validate_state_successor(completed, successor, run_attempt_id=ATTEMPT_ID, fence=1)


def test_waiting_summary_must_match_native_deferred_request_kinds() -> None:
    initial = initial_state()
    payload = initial.model_dump(mode="python")
    payload.update(
        checkpoint_seq=1,
        checkpoint_kind="waiting",
        input_disposition="applied",
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


@pytest.mark.parametrize("limits", [None, UsageLimits(request_limit=4), UsageLimits(request_limit=1)])
def test_checkpoint_cannot_change_accepted_usage_limits(limits: UsageLimits | None) -> None:
    initial = initial_state().model_copy(update={"usage_limits": UsageLimits(request_limit=3)})
    successor = progress_state(initial).model_copy(update={"usage_limits": limits})
    with pytest.raises(ValueError, match="usage_limits"):
        validate_state_successor(initial, successor, run_attempt_id=ATTEMPT_ID, fence=1)
