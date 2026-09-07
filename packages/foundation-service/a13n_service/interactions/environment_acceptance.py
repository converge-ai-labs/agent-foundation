"""Run selections and Environment usage share the existing Run transaction."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.environments.domain import EnvironmentSelection
from a13n_service.environments.errors import invalid_environment
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.environments.selection import Omitted
from a13n_service.environments.usage import lock_run_environments
from a13n_service.interactions.domain import Run, RunInputKind
from a13n_service.interactions.input import (
    AcceptedBinaryContent,
    BinaryContentDelivery,
    PathBinarySource,
)
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import run_state_key
from a13n_service.interactions.records import run_record
from a13n_service.interactions.state import RunStateEnvelope
from a13n_service.object_retention.persistence import require_object_publications

from .environment_selection import select_run_environment


async def add_run_with_environment(
    database: AsyncSession,
    *,
    run: Run,
    state: RunStateEnvelope,
    workspace_id: str,
    choice: EnvironmentSelection | Omitted | None = Omitted.UNSET,
) -> RunRecord:
    keys = [run_state_key(run.organization_id, run.id)]
    if run.input_object is not None:
        keys.append(run.input_object.object_key)
    await require_object_publications(database, keys)
    await database.flush()
    run = await select_run_environment(database, run=run, workspace_id=workspace_id, choice=choice)
    input_value = run.input if run.input_kind is RunInputKind.agent_input else None
    if run.input_kind is RunInputKind.waiting_continue and isinstance(run.input, dict):
        input_value = run.input.get("input")
    contents = input_value.get("content") if isinstance(input_value, dict) else None
    if isinstance(contents, list):
        for content in contents:
            if not isinstance(content, dict) or content.get("type") != "binary":
                continue
            block = AcceptedBinaryContent.model_validate(content)
            if isinstance(block.source, PathBinarySource) and run.environment_id is None:
                raise invalid_environment("Path input requires a selected Environment")
            if block.delivery is BinaryContentDelivery.environment_path and (
                run.environment_id is None or run.environment_access == "read_only"
            ):
                raise invalid_environment("Environment-path delivery requires a writable Environment")
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
                EnvironmentRecord.organization_id == run.organization_id,
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
