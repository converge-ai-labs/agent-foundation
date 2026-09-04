"""Transactional Environment target identity and lifecycle helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import (
    EnvironmentTargetRetentionBehavior,
    EnvironmentTargetStatus,
    new_environment_target_id,
)
from .models import EnvironmentTargetRecord

DEFAULT_TARGET_RETIRE_GRACE = timedelta(hours=1)
DEFAULT_KEEPALIVE_SAFETY_MARGIN = timedelta(minutes=2)


async def upsert_environment_target(
    database: AsyncSession,
    *,
    provider_key: str,
    identity_schema_version: str,
    target_key: str,
    target_identity_digest_sha256: str,
    retention_behavior: EnvironmentTargetRetentionBehavior,
    now: datetime,
) -> EnvironmentTargetRecord:
    """Upsert one tenant-neutral target without performing provider I/O."""

    identity = (
        EnvironmentTargetRecord.provider_key == provider_key,
        EnvironmentTargetRecord.identity_schema_version == identity_schema_version,
        EnvironmentTargetRecord.target_identity_digest_sha256 == target_identity_digest_sha256,
    )
    existing = await database.scalar(select(EnvironmentTargetRecord).where(*identity))
    if existing is None:
        values = {
            "id": new_environment_target_id(),
            "provider_key": provider_key,
            "identity_schema_version": identity_schema_version,
            "target_key": target_key,
            "target_identity_digest_sha256": target_identity_digest_sha256,
            "retention_behavior": retention_behavior.value,
            "status": EnvironmentTargetStatus.idle.value,
            "active_run_count": 0,
            "idle_at": now,
            "retire_after": now + DEFAULT_TARGET_RETIRE_GRACE,
            "keeper_claim_generation": 0,
            "keeper_owner_worker_generation": None,
            "keeper_lease_expires_at": None,
            "keeper_source_binding_id": None,
            "operation_generation": 0,
            "operation_id": None,
            "requested_alive_until": None,
            "acknowledged_alive_until": None,
            "next_keepalive_at": None,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        }
        table = cast(Table, EnvironmentTargetRecord.__table__)
        dialect_name = database.get_bind().dialect.name
        if dialect_name == "postgresql":
            statement = (
                postgresql_insert(table)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=(
                        table.c.provider_key,
                        table.c.identity_schema_version,
                        table.c.target_identity_digest_sha256,
                    )
                )
            )
        elif dialect_name == "sqlite":
            statement = (
                sqlite_insert(table)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=(
                        table.c.provider_key,
                        table.c.identity_schema_version,
                        table.c.target_identity_digest_sha256,
                    )
                )
            )
        else:
            raise RuntimeError(f"unsupported Environment target upsert dialect: {dialect_name}")
        await database.execute(statement)
        existing = await database.scalar(select(EnvironmentTargetRecord).where(*identity))
    if existing is None:
        raise RuntimeError("Environment target upsert did not produce a readable row")
    if existing.target_key != target_key:
        raise ValueError("Environment target digest resolved to a different protected target key")
    if existing.retention_behavior != retention_behavior.value:
        raise ValueError("Environment target retention behavior changed for an existing identity")
    return existing


def activate_environment_target(target: EnvironmentTargetRecord, *, now: datetime) -> None:
    """Add one newly accepted Run contribution while holding the target row lock."""

    if target.active_run_count < 0:
        raise ValueError("Environment target active Run count is corrupt")
    target.active_run_count += 1
    target.status = EnvironmentTargetStatus.active.value
    target.idle_at = None
    target.retire_after = None
    if target.retention_behavior == EnvironmentTargetRetentionBehavior.while_execution_active.value:
        target.next_keepalive_at = now
    target.updated_at = now


def deactivate_environment_target(target: EnvironmentTargetRecord, *, now: datetime) -> None:
    """Remove one active Run contribution while holding the target row lock."""

    if target.active_run_count <= 0 or target.status != EnvironmentTargetStatus.active.value:
        raise ValueError("Environment target has no active Run contribution to remove")
    target.active_run_count -= 1
    target.updated_at = now
    if target.active_run_count > 0:
        return
    target.status = EnvironmentTargetStatus.idle.value
    target.idle_at = now
    target.next_keepalive_at = None
    retire_after = now + DEFAULT_TARGET_RETIRE_GRACE
    if target.keeper_lease_expires_at is not None:
        retire_after = max(retire_after, _utc(target.keeper_lease_expires_at) + DEFAULT_KEEPALIVE_SAFETY_MARGIN)
    if target.requested_alive_until is not None:
        retire_after = max(retire_after, _utc(target.requested_alive_until) + DEFAULT_KEEPALIVE_SAFETY_MARGIN)
    target.retire_after = retire_after


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "DEFAULT_KEEPALIVE_SAFETY_MARGIN",
    "DEFAULT_TARGET_RETIRE_GRACE",
    "activate_environment_target",
    "deactivate_environment_target",
    "upsert_environment_target",
]
