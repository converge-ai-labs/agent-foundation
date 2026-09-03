"""Canonical immutable Environment binding created with an accepted Run."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import EnvironmentExecutionConfig
from a13n_service.environments.domain import (
    EnvironmentAccess,
    environment_logical_digest,
    new_run_environment_binding_id,
)
from a13n_service.environments.models import RunEnvironmentBindingRecord

from .domain import Run
from .records import run_record
from .state import RunStateEnvelope


async def add_run_with_environment_binding(
    database: AsyncSession,
    *,
    run: Run,
    state: RunStateEnvelope,
    workspace_id: str,
) -> None:
    """Insert the Run first, then stage its optional binding in the same transaction."""

    database.add(run_record(run))
    await database.flush()
    binding = environment_binding_record(run, state, workspace_id=workspace_id)
    if binding is not None:
        database.add(binding)


def environment_binding_record(
    run: Run,
    state: RunStateEnvelope,
    *,
    workspace_id: str,
) -> RunEnvironmentBindingRecord | None:
    environment = state.effective_agent_config.resolved_environment
    if environment is None:
        return None
    _validate_execution_config(environment)
    return RunEnvironmentBindingRecord(
        id=new_run_environment_binding_id(),
        organization_id=run.tenant_id,
        workspace_id=workspace_id,
        run_id=run.id,
        mount_name="workspace",
        source_environment_revision_id=environment.source_environment_revision_id,
        provider_key=environment.connection.provider_key,
        target_key=environment.target_key,
        environment_execution_config_digest_sha256=environment.logical_digest_sha256,
        created_at=run.created_at,
    )


async def verify_environment_binding(
    database: AsyncSession,
    *,
    run: Run,
    state: RunStateEnvelope,
) -> None:
    record = await database.scalar(
        select(RunEnvironmentBindingRecord).where(
            RunEnvironmentBindingRecord.organization_id == run.tenant_id,
            RunEnvironmentBindingRecord.run_id == run.id,
        )
    )
    environment = state.effective_agent_config.resolved_environment
    if environment is None:
        if record is not None:
            raise ValueError("Run without an Environment has an unexpected binding")
        return
    _validate_execution_config(environment)
    if record is None or (
        record.mount_name,
        record.source_environment_revision_id,
        record.provider_key,
        record.target_key,
        record.environment_execution_config_digest_sha256,
    ) != (
        "workspace",
        environment.source_environment_revision_id,
        environment.connection.provider_key,
        environment.target_key,
        environment.logical_digest_sha256,
    ):
        raise ValueError("Run Environment binding does not match the accepted execution configuration")


def _validate_execution_config(environment: EnvironmentExecutionConfig) -> None:
    expected_digest = environment_logical_digest(
        connection=environment.connection,
        provider_package_revision_id=environment.provider_package_revision_id,
        provider_lock=environment.provider_lock,
        credential_bindings=environment.credential_bindings,
        access=EnvironmentAccess(environment.access),
        target_key=environment.target_key,
    )
    if environment.logical_digest_sha256 != expected_digest:
        raise ValueError("Run Environment execution configuration digest is invalid")


__all__ = [
    "add_run_with_environment_binding",
    "environment_binding_record",
    "verify_environment_binding",
]
