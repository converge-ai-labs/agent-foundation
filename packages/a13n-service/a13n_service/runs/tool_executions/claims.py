"""Fenced ownership transfer of native Shell supervision, never command admission."""

import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from a13n_harness.environment.process_recovery import ShellExecutionState
from a13n_harness.tool_execution import StoredToolExecution
from sqlalchemy import select

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import lock, now, transaction
from a13n_service.runs.attempts import Lease
from a13n_service.runs.environments import contexts
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import AttemptRow, ToolExecutionRow
from a13n_service.runs.tool_executions.store import observation


@dataclass(frozen=True, slots=True)
class ShellClaim:
    lease: Lease
    execution: StoredToolExecution


async def claim_shells(runtime: Runtime, *, worker_id: str, limit: int) -> list[ShellClaim]:
    """Claim expired native supervisors even when their originating Runs have sealed.

    Cancellation remains durable and is executed by the successor. A claim itself
    neither attaches a Session nor interprets missing process-start evidence.
    """
    claims: list[ShellClaim] = []
    async with transaction(runtime.storage) as session:
        current = await now(session)
        rows = list(
            await session.scalars(
                select(ToolExecutionRow)
                .where(
                    ToolExecutionRow.completed_at.is_(None),
                    ToolExecutionRow.shell.is_not(None),
                    ToolExecutionRow.id.not_in([record.execution_id for record in runtime.executions.active]),
                    ToolExecutionRow.lease_expires_at <= current,
                    contexts.claimable(ToolExecutionRow.thread_id, worker_id),
                )
                .order_by(ToolExecutionRow.lease_expires_at, ToolExecutionRow.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        numbers = (
            {
                attempt_id: number
                for attempt_id, number in await session.execute(
                    select(AttemptRow.id, AttemptRow.number).where(AttemptRow.id.in_({row.attempt_id for row in rows}))
                )
            }
            if rows
            else {}
        )
        owned = await contexts.claim(session, {row.thread_id for row in rows}, worker_id)
        current = await now(session)
        for row in rows:
            if row.thread_id not in owned:
                continue
            token = secrets.token_urlsafe(32)
            row.worker_id = worker_id
            row.lease_token_hash = secret_hash(token)
            row.lease_expires_at = current + timedelta(seconds=runtime.settings.worker.lease_seconds)
            record = observation(row).model_copy(update={"foreground_released": True})
            row.record = record.model_dump(mode="json")
            claims.append(
                ShellClaim(
                    Lease(
                        run_id=row.run_id,
                        attempt_id=row.attempt_id,
                        thread_id=row.thread_id,
                        organization_id=row.organization_id,
                        workspace_id=row.workspace_id,
                        number=numbers[row.attempt_id],
                        worker_id=worker_id,
                        token=token,
                    ),
                    StoredToolExecution(record, shell=ShellExecutionState.model_validate(row.shell)),
                )
            )
    return claims


async def relinquish_shell(
    runtime: Runtime, lease: Lease, execution_id: str, *, retry_after_seconds: float = 0
) -> None:
    """Make detached supervision claimable; a late old-owner release changes nothing."""
    async with transaction(runtime.storage) as session:
        row = await lock(session, ToolExecutionRow, execution_id)
        if (
            row is not None
            and row.thread_id == lease.thread_id
            and row.worker_id == lease.worker_id
            and hmac.compare_digest(row.lease_token_hash, secret_hash(lease.token))
            and row.completed_at is None
            and row.shell is not None
        ):
            row.lease_expires_at = await now(session) + timedelta(seconds=retry_after_seconds)
            if retry_after_seconds == 0:
                runtime.wake_workers(session)
