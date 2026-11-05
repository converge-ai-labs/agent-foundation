"""Thread-local Envd continuity and worker affinity under existing execution leases.

A Thread's overlapping Runs and retained tools share one worker. Their ordinary
leases keep that affinity alive; idle contexts have no heartbeat or extra lease.
Claims lock run/attempt or tool execution before this row, never the Thread row.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

from a13n_harness import RunConfiguration
from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState
from sqlalchemy import SQLColumnExpression, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from a13n_service.infra.db import lock, transaction
from a13n_service.runs.attempts import Lease, LeaseLost
from a13n_service.runs.environments.tables import ThreadExecutionContextRow
from a13n_service.runs.schemas import EnvironmentMount, canonical_json
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow, ToolExecutionRow

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime


def live_owner(
    thread_id: str | SQLColumnExpression[str], worker_id: str | SQLColumnExpression[str | None]
) -> ColumnElement[bool]:
    """An existing, unexpired execution lease is the only source of worker affinity."""
    run = aliased(RunRow)
    tool = aliased(ToolExecutionRow)
    return or_(
        exists(
            select(AttemptRow.id)
            .join(run, run.current_attempt_id == AttemptRow.id)
            .where(
                run.thread_id == thread_id,
                run.status == "running",
                AttemptRow.status.in_(("leased", "running")),
                AttemptRow.worker_id == worker_id,
                AttemptRow.lease_expires_at > func.clock_timestamp(),
            )
        ),
        exists(
            select(tool.id).where(
                tool.thread_id == thread_id,
                tool.worker_id == worker_id,
                or_(tool.environment_mounts != [], tool.shell.is_not(None)),
                tool.completed_at.is_(None),
                tool.lease_expires_at > func.clock_timestamp(),
            )
        ),
    )


def claimable(thread_id: SQLColumnExpression[str], worker_id: str) -> ColumnElement[bool]:
    """Exclude another live owner before LIMIT, so its work cannot starve unrelated Threads."""
    context = ThreadExecutionContextRow
    return ~exists(
        select(context.thread_id).where(
            context.thread_id == thread_id,
            context.worker_id != worker_id,
            live_owner(context.thread_id, context.worker_id),
        )
    )


async def prepare(session: AsyncSession, thread: ThreadRow) -> None:
    """Acceptance already holds Thread; create the FK-bearing row before worker claims."""
    await session.execute(
        insert(ThreadExecutionContextRow)
        .values(thread_id=thread.id, organization_id=thread.organization_id, workspace_id=thread.workspace_id)
        .on_conflict_do_nothing(index_elements=["thread_id"])
    )


async def claim(session: AsyncSession, thread_id: str, worker_id: str) -> bool:
    row = await session.scalar(
        select(ThreadExecutionContextRow)
        .where(ThreadExecutionContextRow.thread_id == thread_id)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        return False
    if row.worker_id == worker_id:
        return True
    if row.worker_id is not None and await session.scalar(select(live_owner(thread_id, row.worker_id))):
        return False
    row.worker_id = worker_id
    row.generation += 1
    return True


def binding_key(mount: EnvironmentMount, configuration: RunConfiguration) -> str:
    """Frozen bindings and endpoint policy separate incompatible logical contexts."""
    return hashlib.sha256(
        canonical_json({"mount": mount.model_dump(mode="json"), "configuration": configuration.model_dump(mode="json")})
    ).hexdigest()


class Contexts:
    """Process-local shared clients backed by the Thread's durable ownership generation."""

    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        # Sources/borrowers own live contexts; idle history lives only in PostgreSQL.
        self._sessions: WeakValueDictionary[tuple[str, int, str], EnvdSessionRecovery] = WeakValueDictionary()
        self._lock = asyncio.Lock()

    async def get(self, lease: Lease, mount: EnvironmentMount, configuration: RunConfiguration) -> EnvdSessionRecovery:
        binding = binding_key(mount, configuration)
        async with self._lock:
            async with transaction(self.runtime.storage) as session:
                row = await lock(session, ThreadExecutionContextRow, lease.thread_id)
                if row is None or row.worker_id != lease.worker_id:
                    raise LeaseLost()
                generation = row.generation
                saved = row.sessions.get(binding)
            key = (lease.thread_id, generation, binding)
            recovery = self._sessions.get(key)
            if recovery is None:

                async def checkpoint(state: EnvdSessionState) -> None:
                    async with transaction(self.runtime.storage) as session:
                        row = await lock(session, ThreadExecutionContextRow, lease.thread_id)
                        if (
                            row is None
                            or row.worker_id != lease.worker_id
                            or row.generation != generation
                            or not await session.scalar(select(live_owner(lease.thread_id, lease.worker_id)))
                        ):
                            raise LeaseLost()
                        row.sessions = {**row.sessions, binding: state.model_dump(mode="json")}

                recovery = EnvdSessionRecovery(
                    None if saved is None else EnvdSessionState.model_validate(saved), checkpoint=checkpoint
                )
                self._sessions[key] = recovery
            return recovery
