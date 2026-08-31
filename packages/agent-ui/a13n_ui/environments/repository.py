"""Last-write-wins persistence for Session Environment resources."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_ui.composition import ResolvedEnvironmentSnapshot
from a13n_ui.configuration.models import canonical_digest
from a13n_ui.errors import EnvironmentLifecycleError, StoreIntegrityError
from a13n_ui.storage.database import short_session, transaction
from a13n_ui.storage.models import SessionEnvironmentResourceRecord
from a13n_ui.storage.runtime import LocalStore

from .models import (
    EnvironmentAvailability,
    EnvironmentResourceStatus,
    SessionEnvironmentResource,
)


async def seed_environment_resources(
    database_session: AsyncSession,
    *,
    session_id: str,
    snapshot: ResolvedEnvironmentSnapshot,
    created_at: datetime,
) -> None:
    """Create one simple provider-state row per desired mount."""

    for mount in snapshot.mounts:
        spec_digest = canonical_digest(
            {
                "provider_key": mount.provider_key,
                "schema_version": mount.provider_schema_version,
                "parameters": mount.normalized_parameters,
            }
        )
        database_session.add(
            SessionEnvironmentResourceRecord(
                session_id=session_id,
                mount_name=mount.mount_name,
                model_alias=mount.model_alias,
                permission_ceiling_json=_json(sorted(mount.permission_ceiling)),
                provider_key=mount.provider_key,
                provider_schema_version=mount.provider_schema_version,
                provider_spec_digest=spec_digest,
                provider_parameters_json=_json(mount.normalized_parameters),
                resource_allocation=mount.lifecycle_capabilities.resource_allocation,
                status=EnvironmentResourceStatus.unprovisioned.value,
                provider_state_digest=None,
                failure_json=None,
                updated_at=created_at,
            )
        )


class EnvironmentRepository:
    """Read and replace latest Session Environment resource state."""

    def __init__(self, store: LocalStore) -> None:
        self._store = store

    async def availability(self, session_id: str) -> EnvironmentAvailability:
        resources = await self.resources(session_id)
        return EnvironmentAvailability(
            session_id=session_id,
            resources=resources,
            ready=all(item.status is EnvironmentResourceStatus.available for item in resources),
        )

    async def resources(self, session_id: str) -> tuple[SessionEnvironmentResource, ...]:
        async with short_session(self._store.database.sessions) as database_session:
            rows = tuple(
                (
                    await database_session.execute(
                        select(SessionEnvironmentResourceRecord)
                        .where(SessionEnvironmentResourceRecord.session_id == session_id)
                        .order_by(SessionEnvironmentResourceRecord.mount_name)
                    )
                ).scalars()
            )
        return tuple(_resource_view(row) for row in rows)

    async def resource(self, session_id: str, mount_name: str) -> SessionEnvironmentResource:
        async with short_session(self._store.database.sessions) as database_session:
            row = await database_session.get(SessionEnvironmentResourceRecord, (session_id, mount_name))
        if row is None:
            raise EnvironmentLifecycleError(
                "The selected Session Environment resource does not exist.",
                code="environment_resource_missing",
            )
        return _resource_view(row)

    async def provider_spec(
        self,
        session_id: str,
        mount_name: str,
    ) -> tuple[str, str, str, dict[str, JsonValue]]:
        async with short_session(self._store.database.sessions) as database_session:
            row = await database_session.get(SessionEnvironmentResourceRecord, (session_id, mount_name))
        if row is None:
            raise EnvironmentLifecycleError(
                "The selected Session Environment resource does not exist.",
                code="environment_resource_missing",
            )
        parameters = _load_json(row.provider_parameters_json)
        if not isinstance(parameters, dict):
            raise StoreIntegrityError(
                "Stored Environment provider parameters have the wrong shape.",
                code="environment_resource_invalid",
            )
        return row.provider_key, row.provider_schema_version, row.provider_spec_digest, parameters

    async def save(
        self,
        session_id: str,
        mount_name: str,
        *,
        status: EnvironmentResourceStatus,
        provider_state_digest: str | None,
        failure: JsonValue | None = None,
    ) -> SessionEnvironmentResource:
        async with transaction(self._store.database.sessions) as database_session:
            row = await database_session.get(SessionEnvironmentResourceRecord, (session_id, mount_name))
            if row is None:
                raise EnvironmentLifecycleError(
                    "The selected Session Environment resource does not exist.",
                    code="environment_resource_missing",
                )
            row.status = status.value
            row.provider_state_digest = provider_state_digest
            row.failure_json = _json(failure) if failure is not None else None
            from datetime import UTC, datetime

            row.updated_at = datetime.now(UTC)
        return await self.resource(session_id, mount_name)


def _resource_view(row: SessionEnvironmentResourceRecord) -> SessionEnvironmentResource:
    try:
        ceiling = _load_json(row.permission_ceiling_json)
        if not isinstance(ceiling, list) or not all(isinstance(item, str) for item in ceiling):
            raise ValueError
        return SessionEnvironmentResource(
            session_id=row.session_id,
            mount_name=row.mount_name,
            model_alias=row.model_alias,
            permission_ceiling=frozenset(ceiling),
            provider_key=row.provider_key,
            provider_schema_version=row.provider_schema_version,
            provider_spec_digest=row.provider_spec_digest,
            resource_allocation=row.resource_allocation,
            status=EnvironmentResourceStatus(row.status),
            provider_state_digest=row.provider_state_digest,
            failure=TypeAdapter(JsonValue).validate_python(_load_json(row.failure_json)),
            updated_at=row.updated_at,
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise StoreIntegrityError(
            "A stored Session Environment resource is invalid.",
            code="environment_resource_invalid",
        ) from exc


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None) -> object:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise StoreIntegrityError(
            "Stored Environment JSON is invalid.",
            code="environment_resource_invalid",
        ) from exc


__all__ = ["EnvironmentRepository", "seed_environment_resources"]
