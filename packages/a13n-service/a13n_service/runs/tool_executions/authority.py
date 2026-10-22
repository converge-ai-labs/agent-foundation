"""Independent retained-call leases; ordinary attempt fencing stays unchanged."""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from a13n_harness.environment.models import EnvironmentAction
from a13n_harness.tool_execution import current_execution
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.attempts import Lease, LeaseLost, authorize_execution, execution_principals, lock_lease
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import AttemptRow, RunRow, ToolExecutionRow
from a13n_service.tenancy.authorize import Principal

if TYPE_CHECKING:
    from a13n_service.runs.tool_executions.routing import Caller


async def lock_execution(
    session: AsyncSession, lease: Lease, execution_id: str, *, cleanup: bool = False
) -> tuple[RunRow, ToolExecutionRow, datetime]:
    run = await lock(session, RunRow, lease.run_id)
    row = await lock(session, ToolExecutionRow, execution_id)
    current = await now(session)
    if (
        run is None
        or row is None
        or row.run_id != lease.run_id
        or row.attempt_id != lease.attempt_id
        or row.thread_id != lease.thread_id
        or row.worker_id != lease.worker_id
        or not hmac.compare_digest(row.lease_token_hash, secret_hash(lease.token))
        or row.completed_at is not None
        or row.lease_expires_at <= current
        or (not cleanup and (row.cancel_requested_at is not None or run.cancel_requested_at is not None))
    ):
        raise LeaseLost()
    return run, row, current


async def lock_owner(session: AsyncSession, runtime: Runtime, lease: Lease) -> tuple[RunRow, Principal]:
    """Fence a dispatch under its attempt or retained execution and observe current authority once."""
    record = current_execution()
    if record is None:
        run, _, _ = await lock_lease(session, lease)
        if run.cancel_requested_at is not None:
            raise LeaseLost()
    else:
        run, _, _ = await lock_execution(session, lease, record.execution_id)
    return run, await authorize_execution(session, runtime.access, run)


async def prove(runtime: Runtime, lease: Lease) -> Principal:
    async with transaction(runtime.storage) as session:
        _, principal = await lock_owner(session, runtime, lease)
        return principal


async def authorize_control(
    session: AsyncSession,
    runtime: Runtime,
    caller: Caller,
    *,
    thread_id: str,
    environment_id: str,
    actions: frozenset[EnvironmentAction],
) -> None:
    """Recheck a trusted worker's caller snapshot immediately before native dispatch.

    The sending worker proves its token and holds current Environment guards.
    Tokens stay process-local; this receiver checks durable eligibility and the
    frozen mount ceiling rather than trusting a new Thread selection.
    """
    run = await session.get(RunRow, caller.run_id)
    current = await now(session)
    if run is None or run.thread_id != thread_id or run.cancel_requested_at is not None:
        raise LeaseLost()
    if caller.execution_id is None:
        attempt = await session.get(AttemptRow, caller.attempt_id)
        if (
            attempt is None
            or attempt.run_id != run.id
            or run.status != "running"
            or run.current_attempt_id != attempt.id
            or attempt.status not in {"leased", "running"}
            or attempt.worker_id != caller.worker_id
            or attempt.lease_expires_at <= current
        ):
            raise LeaseLost()
    else:
        execution = await session.get(ToolExecutionRow, caller.execution_id)
        if (
            execution is None
            or execution.run_id != run.id
            or execution.attempt_id != caller.attempt_id
            or execution.worker_id != caller.worker_id
            or execution.completed_at is not None
            or execution.cancel_requested_at is not None
            or execution.lease_expires_at <= current
        ):
            raise LeaseLost()
    await authorize_execution(session, runtime.access, run)
    mounts = [EnvironmentMount.model_validate(value) for value in run.environment_mounts]
    if not any(
        mount.environment_id == environment_id and actions <= mount.permission_ceiling.operations for mount in mounts
    ):
        raise LeaseLost()


async def heartbeat(runtime: Runtime, worker_id: str) -> tuple[tuple[tuple[str, str], ...], set[str]]:
    """Renew locally active calls through cleanup/publication; report only confirmed renewals."""
    active = {record.execution_id for record in runtime.executions.active}
    async with transaction(runtime.storage) as session:
        rows = list(
            await session.scalars(
                select(ToolExecutionRow)
                .where(
                    ToolExecutionRow.worker_id == worker_id,
                    ToolExecutionRow.id.in_(active),
                    ToolExecutionRow.completed_at.is_(None),
                )
                .with_for_update()
            )
        )
        runs = {
            run.id: run
            for run in await session.scalars(
                select(RunRow).where(
                    RunRow.id.in_({row.run_id for row in rows}),
                )
            )
        }
        principals = await execution_principals(session, runtime.access, runs.values())
        current = await now(session)
        cancel: list[tuple[str, str]] = []
        renewed: set[str] = set()
        for row in rows:
            run = runs[row.run_id]
            if row.lease_expires_at <= current:
                cancel.append((row.thread_id, row.id))
                continue
            if run.cancel_requested_at is not None or isinstance(principals[run.id], ServiceError):
                row.cancel_requested_at = row.cancel_requested_at or current
            if row.cancel_requested_at is not None:
                cancel.append((row.thread_id, row.id))
            # A cancellation request does not release Environment use or permit owner-loss reconciliation.
            row.lease_expires_at = current + timedelta(seconds=runtime.settings.worker.lease_seconds)
            renewed.add(row.id)
        return tuple(cancel), renewed
