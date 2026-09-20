"""Run selections and Environment usage share the existing Run transaction."""

from __future__ import annotations

from a13n_harness.providers.environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.execution_graph import inline_child_executions
from a13n_service.environments.errors import invalid_environment
from a13n_service.environments.models import EnvironmentProviderRecord, EnvironmentRecord
from a13n_service.environments.mount_inheritance import inherit_run_mounts
from a13n_service.environments.usage import lock_run_environments
from a13n_service.environments.websocket.admission import OnlineEvidence
from a13n_service.interactions.domain import Run, RunInputKind
from a13n_service.interactions.input import (
    AcceptedBinaryContent,
    BinaryContentDelivery,
    PathBinarySource,
)
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import run_state_key
from a13n_service.interactions.records import run_record
from a13n_service.interactions.state import RunCheckpoint
from a13n_service.object_retention.persistence import require_object_publications

from .environment_selection import EnvironmentIntent, select_run_environment


async def add_run_with_environment(
    database: AsyncSession,
    *,
    run: Run,
    state: RunCheckpoint,
    workspace_id: str,
    intent: EnvironmentIntent,
    online: OnlineEvidence,
) -> RunRecord:
    keys = [run_state_key(run.organization_id, run.id)]
    if run.input_object is not None:
        keys.append(run.input_object.object_key)
    await require_object_publications(database, keys)
    await database.flush()
    run = await select_run_environment(database, run=run, workspace_id=workspace_id, intent=intent, online=online)
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
            if block.delivery is BinaryContentDelivery.environment_path and run.environment_id is None:
                raise invalid_environment("Environment-path delivery requires a writable Environment")
    config = state.effective_agent_config
    requires_writable = bool(config.skills) or any(
        child.effective_config.skills for _, child in inline_child_executions(config).values()
    )
    if requires_writable and run.environment_id is None:
        raise invalid_environment("Managed Skills require a writable Environment")
    thread = await database.get(ThreadRecord, run.thread_id)
    previous = await database.get(RunRecord, thread.current_run_id) if thread and thread.current_run_id else None
    record = run_record(run)
    await lock_run_environments(
        database,
        run=record,
        additional_environment_ids=(previous.environment_id,) if previous and previous.environment_id else (),
        # This Run has not been inserted or inherited any mounts yet.
        mounted_environment_ids=(),
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
        provider = await database.get(EnvironmentProviderRecord, environment.provider_id)
        if provider is None or not provider.enabled:
            raise invalid_environment("Environment Provider is unavailable")
        if provider.type == WEBSOCKET_PROVIDER_KEY:
            online.require(run.organization_id, environment.id)
    database.add(record)
    if thread is not None:
        thread.default_environment_id = run.environment_id
        thread.default_environment_working_directory = run.environment_working_directory
    await database.flush()
    await inherit_run_mounts(database, run=run, workspace_id=workspace_id)
    return record
