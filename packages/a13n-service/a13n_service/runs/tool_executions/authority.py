"""Independent retained-call leases; ordinary attempt fencing stays unchanged."""

from __future__ import annotations

import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from a13n_harness.environment.models import EnvironmentAction
from a13n_harness.tool_execution import current_execution
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.attempts import (
    AuthorityRevoked,
    Lease,
    LeaseLost,
    authorize_execution,
    execution_principals,
    lock_lease,
)
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import AttemptRow, RunRow, ToolExecutionRow
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal

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
        or (row.completed_at is not None and not cleanup)
        or row.lease_expires_at <= current
        or (not cleanup and (row.cancel_requested_at is not None or run.cancel_requested_at is not None))
    ):
        raise LeaseLost()
    return run, row, current


async def lock_owner(
    session: AsyncSession, runtime: Runtime, lease: Lease, *, cleanup: bool = False
) -> tuple[RunRow, Principal]:
    """Fence a dispatch under its attempt or retained execution and observe current authority once."""
    record = current_execution()
    if record is None:
        run, _, _ = await lock_lease(session, lease)
        if run.cancel_requested_at is not None:
            raise LeaseLost()
    else:
        run, _, _ = await lock_execution(session, lease, record.execution_id, cleanup=cleanup)
    return run, await authorize_execution(session, runtime.access, run)


async def prove(runtime: Runtime, lease: Lease, *, cleanup: bool = False) -> Principal:
    async with transaction(runtime.storage) as session:
        _, principal = await lock_owner(session, runtime, lease, cleanup=cleanup)
        return principal


@dataclass(frozen=True, slots=True)
class ControlGrant:
    principal: Principal
    workspace_id: str
    mount: EnvironmentMount
    authority: ExecutionAuthority


async def authorize_control(
    session: AsyncSession,
    runtime: Runtime,
    caller: Caller,
    *,
    thread_id: str,
    environment_id: str,
    actions: frozenset[EnvironmentAction],
    cleanup: bool = False,
) -> ControlGrant:
    """Recheck a trusted worker's caller snapshot immediately before native dispatch.

    The sending worker proves its token and holds current Environment guards.
    Tokens stay process-local; this receiver checks durable eligibility and the
    frozen mount ceiling rather than trusting a new Thread selection.
    """
    snapshot = await ControlSnapshot.load(session, runtime, [caller])
    return snapshot.authorize(
        caller, thread_id=thread_id, environment_id=environment_id, actions=actions, cleanup=cleanup
    )


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    """One operation's database observations; never retained between checks."""

    runs: dict[str, RunRow]
    attempts: dict[str, AttemptRow]
    executions: dict[str, ToolExecutionRow]
    principals: dict[str, Principal | ServiceError]
    current: datetime

    @classmethod
    async def load(cls, session: AsyncSession, runtime: Runtime, callers: Sequence[Caller]) -> ControlSnapshot:
        runs = {
            row.id: row
            for row in await session.scalars(select(RunRow).where(RunRow.id.in_({c.run_id for c in callers})))
        }
        attempt_ids = {c.attempt_id for c in callers if c.execution_id is None}
        execution_ids = {c.execution_id for c in callers if c.execution_id is not None}
        attempts = (
            {row.id: row for row in await session.scalars(select(AttemptRow).where(AttemptRow.id.in_(attempt_ids)))}
            if attempt_ids
            else {}
        )
        executions = (
            {
                row.id: row
                for row in await session.scalars(select(ToolExecutionRow).where(ToolExecutionRow.id.in_(execution_ids)))
            }
            if execution_ids
            else {}
        )
        return cls(
            runs,
            attempts,
            executions,
            await execution_principals(session, runtime.access, runs.values()),
            await now(session),
        )

    def authorize(
        self,
        caller: Caller,
        *,
        thread_id: str,
        environment_id: str,
        actions: frozenset[EnvironmentAction],
        cleanup: bool = False,
    ) -> ControlGrant:
        run = self.owner(caller, thread_id=thread_id, cleanup=cleanup)
        principal = self.principals[run.id]
        if isinstance(principal, ServiceError):
            raise AuthorityRevoked() from principal
        for value in run.environment_mounts:
            if value["environment_id"] == environment_id:
                mount = EnvironmentMount.model_validate(value)
                if actions <= mount.permission_ceiling.operations:
                    return ControlGrant(
                        principal, run.workspace_id, mount, ExecutionAuthority.model_validate(run.authority)
                    )
        raise LeaseLost()

    def owner(self, caller: Caller, *, thread_id: str, cleanup: bool = False) -> RunRow:
        """Check durable ownership separately from permission to admit new work."""
        run = self.runs.get(caller.run_id)
        current = self.current
        if run is None or run.thread_id != thread_id:
            raise LeaseLost()
        if run.cancel_requested_at is not None and not (cleanup and caller.execution_id):
            raise LeaseLost()
        if caller.execution_id is None:
            attempt = self.attempts.get(caller.attempt_id)
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
            execution = self.executions.get(caller.execution_id)
            if (
                execution is None
                or execution.run_id != run.id
                or execution.attempt_id != caller.attempt_id
                or execution.worker_id != caller.worker_id
                or (execution.completed_at is not None and not cleanup)
                or (execution.cancel_requested_at is not None and not cleanup)
                or execution.lease_expires_at <= current
            ):
                raise LeaseLost()
        return run


async def heartbeat(runtime: Runtime, worker_id: str) -> tuple[tuple[tuple[str, str], ...], set[str]]:
    """Renew locally active calls through cleanup/publication; report only confirmed renewals."""
    active = {record.execution_id for record in runtime.executions.active}
    if not active:
        return (), set()
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
                # Missing renewal is owner loss, not an explicit native cancellation.
                # The worker yields recoverable Shells and cancels only other tools.
                continue
            if run.cancel_requested_at is not None or isinstance(principals[run.id], ServiceError):
                row.cancel_requested_at = row.cancel_requested_at or current
            if row.cancel_requested_at is not None:
                cancel.append((row.thread_id, row.id))
            # A cancellation request does not release Environment use or permit owner-loss reconciliation.
            row.lease_expires_at = current + timedelta(seconds=runtime.settings.worker.lease_seconds)
            renewed.add(row.id)
        return tuple(cancel), renewed
