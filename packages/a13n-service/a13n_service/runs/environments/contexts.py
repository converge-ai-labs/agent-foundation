"""Thread-local Envd continuity and worker affinity under existing execution leases.

A Thread's overlapping Runs and retained tools share one worker. Their ordinary
leases keep that affinity alive; idle contexts have no heartbeat or extra lease.
Claims lock run/attempt or tool execution before this row, never the Thread row.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

from a13n_harness.environment.process_recovery import ShellExecutionState
from a13n_harness.environment.session_recovery import EnvdSessionBorrower
from sqlalchemy import SQLColumnExpression, case, exists, func, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from a13n_service.infra.db import lock, transaction
from a13n_service.runs.attempts import Lease, LeaseLost
from a13n_service.runs.environments.bindings import binding_key
from a13n_service.runs.environments.session_owner import AdmittedSession, SessionOwner
from a13n_service.runs.environments.tables import ThreadExecutionContextRow
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow, ToolExecutionRow
from a13n_service.tenancy.authorize import WorkspaceScope

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime


def live_owner(
    thread_id: str | SQLColumnExpression[str],
    worker_id: str | SQLColumnExpression[str | None],
    *,
    mount: ColumnElement | None = None,
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
                true() if mount is None else run.environment_mounts.contains(mount),
            )
        ),
        exists(
            select(tool.id).where(
                tool.thread_id == thread_id,
                tool.worker_id == worker_id,
                or_(tool.environment_mounts != [], tool.shell.is_not(None)),
                tool.completed_at.is_(None),
                tool.lease_expires_at > func.clock_timestamp(),
                true() if mount is None else tool.environment_mounts.contains(mount),
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


async def claim(session: AsyncSession, thread_ids: set[str], worker_id: str) -> set[str]:
    """Lock candidate contexts together, then recheck live leases before one batch transfer."""
    if not thread_ids:
        return set()
    row = ThreadExecutionContextRow
    locked = list(
        await session.scalars(
            select(row.thread_id)
            .where(row.thread_id.in_(thread_ids))
            .order_by(row.thread_id)
            .with_for_update(skip_locked=True)
        )
    )
    if not locked:
        return set()
    return set(
        await session.scalars(
            update(row)
            .where(
                row.thread_id.in_(locked),
                or_(row.worker_id == worker_id, row.worker_id.is_(None), ~live_owner(row.thread_id, row.worker_id)),
            )
            .values(
                worker_id=worker_id,
                generation=case((row.worker_id == worker_id, row.generation), else_=row.generation + 1),
            )
            .returning(row.thread_id)
            .execution_options(synchronize_session=False)
        )
    )


class Contexts:
    """Process-local shared clients backed by the Thread's durable ownership generation."""

    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        # Sources/borrowers own live contexts; idle history lives only in PostgreSQL.
        self._sessions: WeakValueDictionary[tuple[str, int, tuple[str, str]], SessionOwner] = WeakValueDictionary()
        self._lock = asyncio.Lock()

    def active(self) -> tuple[SessionOwner, ...]:
        return tuple(owner for owner in self._sessions.values() if owner.active and owner.sync_key is not None)

    async def get(self, lease: Lease, mount: EnvironmentMount) -> AdmittedSession:
        return await self._get(lease.thread_id, lease.worker_id, mount)

    async def for_control(
        self,
        thread_id: str,
        run_id: str,
        worker_id: str,
        environment_id: str,
        mount_name: str,
        *,
        execution_id: str | None = None,
    ) -> EnvdSessionBorrower:
        """Use the same frozen binding after Control has authorized its caller."""
        async with transaction(self.runtime.storage) as session:
            run = await session.get(RunRow, run_id)
            if run is None or run.thread_id != thread_id:
                raise LeaseLost()
            original_session_id = None
            mounts = run.environment_mounts
            if execution_id is not None:
                execution = await session.get(ToolExecutionRow, execution_id)
                if execution is None or execution.run_id != run_id or execution.shell is None:
                    raise LeaseLost()
                shell = ShellExecutionState.model_validate(execution.shell)
                if shell.mount_name != mount_name:
                    raise LeaseLost()
                original_session_id = shell.process.session_id
                mounts = execution.environment_mounts
            mount = next(
                (
                    EnvironmentMount.model_validate(value)
                    for value in mounts
                    if value["environment_id"] == environment_id and value["name"] == mount_name
                ),
                None,
            )
            if mount is None:
                raise LeaseLost()
        recovery = await self._get(thread_id, worker_id, mount)
        return recovery if original_session_id is None else recovery.exact(original_session_id)

    async def _get(self, thread_id: str, worker_id: str, mount: EnvironmentMount) -> AdmittedSession:
        binding = binding_key(mount)
        async with self._lock:
            async with transaction(self.runtime.storage) as session:
                row = await lock(session, ThreadExecutionContextRow, thread_id)
                if row is None or row.worker_id != worker_id:
                    raise LeaseLost()
                generation = row.generation
                scope = WorkspaceScope(row.organization_id, row.workspace_id)
            key = (thread_id, generation, binding)
            owner = self._sessions.get(key)
            if owner is None:
                owner = SessionOwner(self.runtime, thread_id, worker_id, generation, binding, scope)
                self._sessions[key] = owner
            return AdmittedSession(owner, mount)
