"""Shared relational transition primitives for the interaction domain."""

from __future__ import annotations

from datetime import datetime

from a13n_harness import SafeFailure

from .domain import RecoveryUsage, RunAttemptStatus, RunAttemptYieldReason, RunStatus
from .models import RunAttemptRecord, RunRecord, ThreadRecord


def terminalize_attempt(
    attempt: RunAttemptRecord,
    status: RunAttemptStatus,
    now: datetime,
    *,
    failure: SafeFailure | None = None,
    yield_reason: RunAttemptYieldReason | None = None,
) -> None:
    if status not in {
        RunAttemptStatus.succeeded,
        RunAttemptStatus.yielded,
        RunAttemptStatus.failed,
        RunAttemptStatus.cancelled,
    }:
        raise ValueError("RunAttempt terminalization requires a terminal status")
    if status is RunAttemptStatus.failed and failure is None:
        raise ValueError("failed RunAttempt terminalization requires a failure")
    if status is RunAttemptStatus.yielded and (yield_reason is None or failure is not None):
        raise ValueError("yielded RunAttempt terminalization requires only a yield reason")
    if status not in {RunAttemptStatus.failed, RunAttemptStatus.cancelled} and failure is not None:
        raise ValueError("only failed or cancelled RunAttempt terminalization can carry failure")
    if status is not RunAttemptStatus.yielded and yield_reason is not None:
        raise ValueError("yield reason is valid only for yielded RunAttempt terminalization")
    attempt.status = status.value
    attempt.failure_json = None if failure is None else failure.model_dump(mode="json", by_alias=True)
    attempt.yield_reason = None if yield_reason is None else yield_reason.value
    attempt.finished_at = now
    attempt.lease_expires_at = now
    attempt.updated_at = now
    attempt.version += 1


def charge_attempt_usage(run: RunRecord, attempt: RunAttemptRecord) -> None:
    charged = RecoveryUsage.model_validate(run.usage_charged_json)
    usage = RecoveryUsage.model_validate(attempt.usage_json)
    run.usage_charged_json = charged.plus(usage).model_dump(mode="json")


def seal_failed_run(
    run: RunRecord,
    thread: ThreadRecord,
    failure: SafeFailure,
    now: datetime,
) -> None:
    run.status = RunStatus.failed.value
    run.current_run_attempt_id = None
    run.failure_json = failure.model_dump(mode="json", by_alias=True)
    run.sealed_at = now
    run.updated_at = now
    run.version += 1
    thread.version += 1
    thread.updated_at = now


__all__ = ["charge_attempt_usage", "seal_failed_run", "terminalize_attempt"]
