from __future__ import annotations

from datetime import UTC, datetime

import pytest
from a13n_service.interactions import (
    RecoveryUsage,
    RecoveryUsageLimit,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)
from a13n_service.interactions.state import validate_state_successor
from pydantic import ValidationError

from .conftest import ATTEMPT_ID, initial_state, progress_state

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def test_thread_origin_combinations_are_explicit() -> None:
    root = Thread(
        id="thread-1234567890abcdef1234567890abcdef",
        version=1,
        queue_version=0,
        tenant_id="org_1234567890abcdef",
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
            tenant_id="org_1234567890abcdef",
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
