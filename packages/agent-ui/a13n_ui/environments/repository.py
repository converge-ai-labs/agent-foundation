"""Fenced SQLite persistence for Host-managed Environment resources."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import uuid4

from a13n_environment_provider import EnvironmentManagementAction, EnvironmentOperationContext
from pydantic import JsonValue, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_ui.composition import ResolvedEnvironmentSnapshot
from a13n_ui.configuration.models import canonical_digest
from a13n_ui.errors import EnvironmentLifecycleError, StoreIntegrityError
from a13n_ui.storage.database import short_session, transaction
from a13n_ui.storage.models import (
    HostEnvironmentResourceRecord,
    SessionEnvironmentAssignmentRecord,
)
from a13n_ui.storage.runtime import LocalStore

from .models import (
    EnvironmentAvailability,
    EnvironmentOperationView,
    HostEnvironmentResource,
    HostResourceLifecycleState,
    SessionEnvironmentAssignment,
)

_TRANSITION_BY_ACTION: dict[EnvironmentManagementAction, HostResourceLifecycleState] = {
    EnvironmentManagementAction.CREATE: HostResourceLifecycleState.creating,
    EnvironmentManagementAction.RESUME: HostResourceLifecycleState.resuming,
    EnvironmentManagementAction.PAUSE: HostResourceLifecycleState.pausing,
    EnvironmentManagementAction.DESTROY: HostResourceLifecycleState.destroying,
}


async def seed_environment_assignments(
    database_session: AsyncSession,
    *,
    session_id: str,
    snapshot: ResolvedEnvironmentSnapshot,
    created_at: datetime,
) -> None:
    """Create root assignments and resource authority in the Session transaction."""

    for binding in snapshot.bindings:
        spec_digest = canonical_digest(
            {
                "provider_key": binding.provider_key,
                "schema_version": binding.provider_schema_version,
                "parameters": binding.normalized_parameters,
            }
        )
        if binding.lifecycle_capabilities.resource_allocation == "single_from_spec":
            host_resource_id = f"resource-{spec_digest[:24]}"
        else:
            host_resource_id = f"resource-{uuid4().hex}"
        existing = await database_session.get(HostEnvironmentResourceRecord, host_resource_id)
        parameters_json = _json(binding.normalized_parameters)
        if existing is None:
            database_session.add(
                HostEnvironmentResourceRecord(
                    host_resource_id=host_resource_id,
                    provider_key=binding.provider_key,
                    provider_schema_version=binding.provider_schema_version,
                    provider_spec_digest=spec_digest,
                    binding_parameters_json=parameters_json,
                    resource_allocation=binding.lifecycle_capabilities.resource_allocation,
                    lifecycle_state=HostResourceLifecycleState.unprovisioned.value,
                    operation_fence=0,
                    selected_provider_state_digest=None,
                    last_operation_id=None,
                    last_operation_action=None,
                    last_operation_attempt=None,
                    failure_json=None,
                    updated_at=created_at,
                )
            )
        elif (
            existing.provider_key != binding.provider_key
            or existing.provider_schema_version != binding.provider_schema_version
            or existing.provider_spec_digest != spec_digest
            or existing.binding_parameters_json != parameters_json
            or existing.resource_allocation != binding.lifecycle_capabilities.resource_allocation
        ):
            raise StoreIntegrityError(
                "A canonical Host Environment resource has conflicting provider identity.",
                code="environment_resource_identity_conflict",
            )
        database_session.add(
            SessionEnvironmentAssignmentRecord(
                assignment_id=f"assignment-{uuid4().hex}",
                session_id=session_id,
                binding_name=binding.binding_name,
                model_alias=binding.model_alias,
                permission_ceiling_json=_json(sorted(binding.permission_ceiling)),
                required=binding.required,
                scope_key="root",
                host_resource_id=host_resource_id,
                created_at=created_at,
            )
        )


class EnvironmentRepository:
    """Own Environment resource fences and detached lifecycle projections."""

    def __init__(self, store: LocalStore) -> None:
        self._store = store

    async def availability(self, session_id: str) -> EnvironmentAvailability:
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as database_session:
            assignment_rows = tuple(
                (
                    await database_session.execute(
                        select(SessionEnvironmentAssignmentRecord)
                        .where(SessionEnvironmentAssignmentRecord.session_id == session_id)
                        .order_by(SessionEnvironmentAssignmentRecord.binding_name)
                    )
                ).scalars()
            )
            resource_ids = {row.host_resource_id for row in assignment_rows}
            resource_rows = tuple(
                (
                    await database_session.execute(
                        select(HostEnvironmentResourceRecord).where(
                            HostEnvironmentResourceRecord.host_resource_id.in_(resource_ids)
                        )
                    )
                ).scalars()
                if resource_ids
                else ()
            )
        resources = tuple(_resource_view(row) for row in resource_rows)
        resource_by_id = {item.host_resource_id: item for item in resources}
        assignments = tuple(_assignment_view(row) for row in assignment_rows)
        ready = all(
            not assignment.required
            or (
                resource_by_id.get(assignment.host_resource_id) is not None
                and resource_by_id[assignment.host_resource_id].lifecycle_state is HostResourceLifecycleState.available
            )
            for assignment in assignments
        )
        return EnvironmentAvailability(
            session_id=session_id,
            assignments=assignments,
            resources=resources,
            ready=ready,
        )

    async def resource(self, host_resource_id: str) -> HostEnvironmentResource:
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as database_session:
            row = await database_session.get(HostEnvironmentResourceRecord, host_resource_id)
        if row is None:
            raise EnvironmentLifecycleError(
                "The selected Host Environment resource does not exist.",
                code="environment_resource_missing",
            )
        return _resource_view(row)

    async def provider_spec(
        self,
        host_resource_id: str,
    ) -> tuple[str, str, str, dict[str, JsonValue]]:
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as database_session:
            row = await database_session.get(HostEnvironmentResourceRecord, host_resource_id)
        if row is None:
            raise EnvironmentLifecycleError(
                "The selected Host Environment resource does not exist.",
                code="environment_resource_missing",
            )
        parameters = _load_json(row.binding_parameters_json)
        if not isinstance(parameters, dict):
            raise StoreIntegrityError(
                "Stored Environment provider parameters have the wrong shape.",
                code="environment_resource_invalid",
            )
        return row.provider_key, row.provider_schema_version, row.provider_spec_digest, parameters

    async def begin_operation(
        self,
        host_resource_id: str,
        *,
        expected_fence: int,
        action: EnvironmentManagementAction,
        allowed_states: frozenset[HostResourceLifecycleState],
    ) -> tuple[HostEnvironmentResource, EnvironmentOperationContext]:
        now = datetime.now(UTC)
        operation_id = f"operation-{uuid4().hex}"
        async with transaction(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as database_session:
            row = await database_session.get(HostEnvironmentResourceRecord, host_resource_id)
            if row is None:
                raise EnvironmentLifecycleError(
                    "The selected Host Environment resource does not exist.",
                    code="environment_resource_missing",
                )
            if row.operation_fence != expected_fence:
                raise EnvironmentLifecycleError(
                    "The Environment resource operation fence is stale.",
                    code="environment_operation_conflict",
                    details={"current_fence": row.operation_fence},
                )
            if HostResourceLifecycleState(row.lifecycle_state) not in allowed_states:
                raise EnvironmentLifecycleError(
                    "The Environment resource lifecycle state rejects this operation.",
                    code="environment_operation_invalid",
                    details={"lifecycle_state": row.lifecycle_state},
                )
            row.operation_fence += 1
            row.lifecycle_state = _TRANSITION_BY_ACTION[action].value
            row.last_operation_id = operation_id
            row.last_operation_action = action.value
            row.last_operation_attempt = 1
            row.failure_json = None
            row.updated_at = now
        operation = EnvironmentOperationContext(
            operation_id=operation_id,
            action=action,
            resource_correlation=host_resource_id,
            attempt=1,
        )
        return await self.resource(host_resource_id), operation

    async def complete_state_operation(
        self,
        host_resource_id: str,
        *,
        fence: int,
        operation_id: str,
        state: HostResourceLifecycleState,
        provider_state_digest: str,
    ) -> HostEnvironmentResource:
        if state not in {HostResourceLifecycleState.available, HostResourceLifecycleState.paused}:
            raise ValueError("state completion must select available or paused")
        async with transaction(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as database_session:
            row = await self._matching_operation(database_session, host_resource_id, fence, operation_id)
            row.lifecycle_state = state.value
            row.selected_provider_state_digest = provider_state_digest
            row.failure_json = None
            row.updated_at = datetime.now(UTC)
        return await self.resource(host_resource_id)

    async def complete_absent_operation(
        self,
        host_resource_id: str,
        *,
        fence: int,
        operation_id: str,
        state: Literal[
            HostResourceLifecycleState.unprovisioned,
            HostResourceLifecycleState.destroyed,
            HostResourceLifecycleState.missing,
        ],
    ) -> HostEnvironmentResource:
        async with transaction(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as database_session:
            row = await self._matching_operation(database_session, host_resource_id, fence, operation_id)
            row.lifecycle_state = state.value
            row.selected_provider_state_digest = None
            row.failure_json = None
            row.updated_at = datetime.now(UTC)
        return await self.resource(host_resource_id)

    async def fail_operation(
        self,
        host_resource_id: str,
        *,
        fence: int,
        operation_id: str,
        unknown: bool,
        failure: JsonValue,
    ) -> HostEnvironmentResource:
        async with transaction(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as database_session:
            row = await self._matching_operation(database_session, host_resource_id, fence, operation_id)
            row.lifecycle_state = (
                HostResourceLifecycleState.unknown.value if unknown else HostResourceLifecycleState.failed.value
            )
            row.failure_json = _json(failure)
            row.updated_at = datetime.now(UTC)
        return await self.resource(host_resource_id)

    async def assignment_count(self, host_resource_id: str) -> int:
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as database_session:
            return int(
                (
                    await database_session.execute(
                        select(func.count())
                        .select_from(SessionEnvironmentAssignmentRecord)
                        .where(SessionEnvironmentAssignmentRecord.host_resource_id == host_resource_id)
                    )
                ).scalar_one()
            )

    async def all_resources(self) -> tuple[HostEnvironmentResource, ...]:
        """Return all retained Host resource authority for startup validation."""

        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as database_session:
            rows = tuple(
                (
                    await database_session.execute(
                        select(HostEnvironmentResourceRecord).order_by(HostEnvironmentResourceRecord.host_resource_id)
                    )
                ).scalars()
            )
        return tuple(_resource_view(row) for row in rows)

    async def fail_closed(self, host_resource_id: str, *, failure: JsonValue) -> None:
        """Mark one resource unknown when selected startup authority is invalid."""

        async with transaction(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as database_session:
            row = await database_session.get(HostEnvironmentResourceRecord, host_resource_id)
            if row is None:
                return
            row.lifecycle_state = HostResourceLifecycleState.unknown.value
            row.failure_json = _json(failure)
            row.updated_at = datetime.now(UTC)

    async def resources_in_states(
        self,
        states: frozenset[HostResourceLifecycleState],
    ) -> tuple[HostEnvironmentResource, ...]:
        if not states:
            return ()
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as database_session:
            rows = tuple(
                (
                    await database_session.execute(
                        select(HostEnvironmentResourceRecord)
                        .where(HostEnvironmentResourceRecord.lifecycle_state.in_(item.value for item in states))
                        .order_by(HostEnvironmentResourceRecord.host_resource_id)
                    )
                ).scalars()
            )
        return tuple(_resource_view(row) for row in rows)

    async def detach_session_assignments(self, session_id: str) -> tuple[str, ...]:
        async with transaction(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as database_session:
            resource_ids = tuple(
                dict.fromkeys(
                    (
                        await database_session.execute(
                            select(SessionEnvironmentAssignmentRecord.host_resource_id)
                            .where(SessionEnvironmentAssignmentRecord.session_id == session_id)
                            .order_by(SessionEnvironmentAssignmentRecord.host_resource_id)
                        )
                    ).scalars()
                )
            )
            await database_session.execute(
                delete(SessionEnvironmentAssignmentRecord).where(
                    SessionEnvironmentAssignmentRecord.session_id == session_id
                )
            )
        return resource_ids

    async def _matching_operation(
        self,
        database_session: AsyncSession,
        host_resource_id: str,
        fence: int,
        operation_id: str,
    ) -> HostEnvironmentResourceRecord:
        row = await database_session.get(HostEnvironmentResourceRecord, host_resource_id)
        if row is None:
            raise EnvironmentLifecycleError(
                "The selected Host Environment resource does not exist.",
                code="environment_resource_missing",
            )
        if row.operation_fence != fence or row.last_operation_id != operation_id:
            raise EnvironmentLifecycleError(
                "A stale Environment operation cannot select provider state.",
                code="environment_operation_conflict",
                details={"current_fence": row.operation_fence},
            )
        return row


def _resource_view(row: HostEnvironmentResourceRecord) -> HostEnvironmentResource:
    operation = None
    if row.last_operation_id is not None:
        if row.last_operation_action is None or row.last_operation_attempt is None:
            raise StoreIntegrityError(
                "A Host Environment resource operation record is incomplete.",
                code="environment_resource_invalid",
            )
        operation = EnvironmentOperationView(
            operation_id=row.last_operation_id,
            action=cast(
                Literal["create", "resume", "pause", "destroy"],
                row.last_operation_action,
            ),
            attempt=row.last_operation_attempt,
        )
    try:
        return HostEnvironmentResource(
            host_resource_id=row.host_resource_id,
            provider_key=row.provider_key,
            provider_schema_version=row.provider_schema_version,
            provider_spec_digest=row.provider_spec_digest,
            resource_allocation=cast(
                Literal["single_from_spec", "multiple_from_spec"],
                row.resource_allocation,
            ),
            lifecycle_state=HostResourceLifecycleState(row.lifecycle_state),
            operation_fence=row.operation_fence,
            selected_provider_state_digest=row.selected_provider_state_digest,
            last_operation=operation,
            failure=_load_json(row.failure_json),
            updated_at=row.updated_at,
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise StoreIntegrityError(
            "A Host Environment resource projection is invalid.",
            code="environment_resource_invalid",
        ) from exc


def _assignment_view(row: SessionEnvironmentAssignmentRecord) -> SessionEnvironmentAssignment:
    permissions = _load_json(row.permission_ceiling_json)
    if not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions):
        raise StoreIntegrityError(
            "An Environment assignment permission set is invalid.",
            code="environment_assignment_invalid",
        )
    permission_names = frozenset(cast(list[str], permissions))
    try:
        return SessionEnvironmentAssignment(
            assignment_id=row.assignment_id,
            session_id=row.session_id,
            binding_name=row.binding_name,
            model_alias=row.model_alias,
            permission_ceiling=permission_names,
            required=row.required,
            scope_key=row.scope_key,
            host_resource_id=row.host_resource_id,
            created_at=row.created_at,
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise StoreIntegrityError(
            "An Environment assignment projection is invalid.",
            code="environment_assignment_invalid",
        ) from exc


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _load_json(value: str | None) -> JsonValue | None:
    if value is None:
        return None
    try:
        return cast(JsonValue, json.loads(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise StoreIntegrityError("Stored JSON metadata is malformed.", code="stored_json_invalid") from exc


__all__ = ["EnvironmentRepository", "seed_environment_assignments"]
