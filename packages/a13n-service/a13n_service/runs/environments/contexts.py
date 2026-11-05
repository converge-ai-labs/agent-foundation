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
from sqlalchemy import SQLColumnExpression, case, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from a13n_service.infra.db import lock, transaction
from a13n_service.runs.attempts import Lease, LeaseLost
from a13n_service.runs.environments.tables import ThreadExecutionContextRow
from a13n_service.runs.schemas import EnvironmentMount, RunOptions, canonical_json
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


def binding_key(mount: EnvironmentMount, configuration: RunConfiguration) -> str:
    """Frozen bindings and endpoint policy separate incompatible logical contexts."""
    return hashlib.sha256(
        canonical_json({"mount": mount.model_dump(mode="json"), "configuration": configuration.model_dump(mode="json")})
    ).hexdigest()


async def retained_bindings(session: AsyncSession, lease: Lease) -> set[str]:
    """Keep the continuation head, current Run and bindings needed by unfinished work."""
    last_run = select(ThreadRow.last_run_id).where(ThreadRow.id == lease.thread_id).scalar_subquery()
    runs = select(RunRow.environment_mounts, RunRow.options).where(
        RunRow.thread_id == lease.thread_id,
        or_(RunRow.id == lease.run_id, RunRow.id == last_run, RunRow.status.in_(("accepted", "running", "waiting"))),
    )
    tools = (
        select(ToolExecutionRow.environment_mounts, RunRow.options)
        .join(RunRow, RunRow.id == ToolExecutionRow.run_id)
        .where(ToolExecutionRow.thread_id == lease.thread_id, ToolExecutionRow.completed_at.is_(None))
    )
    bindings: set[str] = set()
    for mounts, options in await session.execute(runs.union_all(tools)):
        configuration = RunOptions.model_validate(options).configuration or RunConfiguration()
        bindings.update(binding_key(EnvironmentMount.model_validate(mount), configuration) for mount in mounts)
    return bindings


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
                        retained = await retained_bindings(session, lease)
                        row.sessions = {
                            **{key: value for key, value in row.sessions.items() if key in retained},
                            binding: state.model_dump(mode="json"),
                        }

                recovery = EnvdSessionRecovery(
                    None if saved is None else EnvdSessionState.model_validate(saved), checkpoint=checkpoint
                )
                self._sessions[key] = recovery
            return recovery
