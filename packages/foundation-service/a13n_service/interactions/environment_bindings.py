"""Canonical immutable Environment binding created with an accepted Run."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import EnvironmentExecutionConfig
from a13n_service.environments.domain import (
    EnvironmentAccess,
    environment_logical_digest,
    new_run_environment_binding_id,
)
from a13n_service.environments.models import EnvironmentTargetRecord, RunEnvironmentBindingRecord
from a13n_service.environments.targets import activate_environment_target, deactivate_environment_target

from .domain import Run, RunStatus
from .models import RunRecord
from .records import run_record
from .state import RunStateEnvelope


async def add_run_with_environment_binding(
    database: AsyncSession,
    *,
    run: Run,
    state: RunStateEnvelope,
    workspace_id: str,
) -> RunRecord:
    """Insert the Run first, then stage its optional binding in the same transaction."""

    record = run_record(run)
    database.add(record)
    await database.flush()
    binding = environment_binding_record(run, state, workspace_id=workspace_id)
    if binding is not None:
        if run.status is not RunStatus.accepted:
            raise ValueError("only a newly accepted Run can activate an Environment target")
        target = await database.scalar(
            select(EnvironmentTargetRecord)
            .where(EnvironmentTargetRecord.id == binding.environment_target_id)
            .with_for_update()
        )
        if target is None or (target.provider_key, target.target_key) != (
            binding.provider_key,
            binding.target_key,
        ):
            raise ValueError("Run Environment binding references an invalid target")
        activate_environment_target(target, now=run.created_at)
        database.add(binding)
    return record


def environment_binding_record(
    run: Run,
    state: RunStateEnvelope,
    *,
    workspace_id: str,
) -> RunEnvironmentBindingRecord | None:
    environment = state.effective_agent_config.resolved_environment
    if environment is None:
        return None
    _validate_execution_config(environment, require_target=True)
    assert environment.environment_target_id is not None
    return RunEnvironmentBindingRecord(
        id=new_run_environment_binding_id(),
        organization_id=run.tenant_id,
        workspace_id=workspace_id,
        run_id=run.id,
        mount_name="workspace",
        source_environment_revision_id=environment.source_environment_revision_id,
        environment_target_id=environment.environment_target_id,
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
    target_mismatch = (
        record is not None
        and environment.environment_target_id is not None
        and record.environment_target_id != environment.environment_target_id
    )
    if (
        record is None
        or target_mismatch
        or (
            record.mount_name,
            record.source_environment_revision_id,
            record.provider_key,
            record.target_key,
            record.environment_execution_config_digest_sha256,
        )
        != (
            "workspace",
            environment.source_environment_revision_id,
            environment.connection.provider_key,
            environment.target_key,
            environment.logical_digest_sha256,
        )
    ):
        raise ValueError("Run Environment binding does not match the accepted execution configuration")


async def deactivate_run_environment(
    database: AsyncSession,
    *,
    run: RunRecord,
    now: datetime,
) -> None:
    """Remove one binding contribution before an active Run is sealed."""

    if run.status not in {RunStatus.accepted.value, RunStatus.running.value}:
        raise ValueError("only an active Run can release its Environment target")
    binding = await database.scalar(
        select(RunEnvironmentBindingRecord).where(
            RunEnvironmentBindingRecord.organization_id == run.tenant_id,
            RunEnvironmentBindingRecord.run_id == run.id,
        )
    )
    if binding is None:
        return
    target = await database.scalar(
        select(EnvironmentTargetRecord)
        .where(EnvironmentTargetRecord.id == binding.environment_target_id)
        .with_for_update()
    )
    if target is None:
        raise ValueError("Run Environment target is missing")
    deactivate_environment_target(target, now=now)


async def lock_run_environment_targets(
    database: AsyncSession,
    *,
    run: RunRecord,
    additional_target_ids: tuple[str, ...] = (),
) -> None:
    """Pre-lock all target rows for a combined Run transition in stable ID order."""

    source_target_id = await database.scalar(
        select(RunEnvironmentBindingRecord.environment_target_id).where(
            RunEnvironmentBindingRecord.organization_id == run.tenant_id,
            RunEnvironmentBindingRecord.run_id == run.id,
        )
    )
    target_ids = tuple(sorted(set(additional_target_ids + ((source_target_id,) if source_target_id else ()))))
    if not target_ids:
        return
    locked = tuple(
        (
            await database.scalars(
                select(EnvironmentTargetRecord)
                .where(EnvironmentTargetRecord.id.in_(target_ids))
                .order_by(EnvironmentTargetRecord.id)
                .with_for_update()
            )
        ).all()
    )
    if tuple(item.id for item in locked) != target_ids:
        raise ValueError("combined Run transition references a missing Environment target")


def _validate_execution_config(
    environment: EnvironmentExecutionConfig,
    *,
    require_target: bool = False,
) -> None:
    if require_target and environment.environment_target_id is None:
        raise ValueError("new Run Environment execution configuration has no target reference")
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
    "deactivate_run_environment",
    "environment_binding_record",
    "lock_run_environment_targets",
    "verify_environment_binding",
]
