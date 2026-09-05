"""Run selections and Environment usage share the existing Run transaction."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.interactions.domain import Run
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.records import run_record
from a13n_service.interactions.state import RunStateEnvelope

from .domain import EnvironmentSelection
from .errors import invalid_environment
from .models import EnvironmentRecord
from .retention import refresh_retention
from .selection import Omitted, select_run_environment


async def add_run_with_environment(
    database: AsyncSession,
    *,
    run: Run,
    state: RunStateEnvelope,
    workspace_id: str,
    choice: EnvironmentSelection | Omitted | None = Omitted.UNSET,
) -> RunRecord:
    await database.flush()
    run = await select_run_environment(database, run=run, workspace_id=workspace_id, choice=choice)
    if state.effective_agent_config.skills and (run.environment_id is None or run.environment_access == "read_only"):
        raise invalid_environment("Managed Skills require a writable Environment")
    if (run.environment_id is None) != (run.environment_access is None):
        raise invalid_environment("Environment selection and access must be supplied together")
    thread = await database.get(ThreadRecord, run.thread_id)
    previous = await database.get(RunRecord, thread.current_run_id) if thread and thread.current_run_id else None
    record = run_record(run)
    await lock_run_environments(
        database,
        run=record,
        additional_environment_ids=(previous.environment_id,) if previous and previous.environment_id else (),
    )
    if run.environment_id is not None:
        environment = await database.scalar(
            select(EnvironmentRecord)
            .where(
                EnvironmentRecord.id == run.environment_id,
                EnvironmentRecord.workspace_id == workspace_id,
                EnvironmentRecord.organization_id == run.tenant_id,
            )
            .with_for_update()
        )
        if environment is None:
            raise invalid_environment("Run references an unavailable Environment")
    database.add(record)
    if thread is not None:
        thread.default_environment_id = run.environment_id
    await database.flush()
    return record


async def schedule_environment_maintenance(database: AsyncSession, *, run: RunRecord, now: datetime) -> None:
    if run.environment_id is None or run.environment_use_started_at is None:
        return
    environment = await database.scalar(
        select(EnvironmentRecord).where(EnvironmentRecord.id == run.environment_id).with_for_update()
    )
    if environment is None:
        raise invalid_environment("Run Environment is missing")
    await refresh_retention(database, environment, now)


async def lock_run_environments(
    database: AsyncSession, *, run: RunRecord, additional_environment_ids: tuple[str, ...] = ()
) -> None:
    ids = sorted(set(additional_environment_ids + ((run.environment_id,) if run.environment_id else ())))
    if ids:
        rows = tuple(
            await database.scalars(
                select(EnvironmentRecord)
                .where(EnvironmentRecord.id.in_(ids))
                .order_by(EnvironmentRecord.id)
                .with_for_update()
            )
        )
        if [row.id for row in rows] != ids:
            raise invalid_environment("Run references a missing Environment")
