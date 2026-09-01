"""Host-owned Environment resource selection and provider-state publication."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from a13n_environment_provider import EnvironmentProviderResourceState
from anyio import Lock
from pydantic import JsonValue

from a13n_ui.errors import EnvironmentLifecycleError, StoreIntegrityError
from a13n_ui.storage.objects import ObjectKind, ObjectRef
from a13n_ui.storage.runtime import LocalStore

from .models import (
    EnvironmentAvailability,
    EnvironmentResourceStatus,
    SessionEnvironmentResource,
    StoredProviderState,
)
from .repository import EnvironmentRepository

_ResourceKey = tuple[str, str]


class EnvironmentService:
    """Select resource rows and publish detached state returned by a Runner."""

    def __init__(self, *, store: LocalStore) -> None:
        self._store = store
        self._repository = EnvironmentRepository(store)
        self._locks: dict[_ResourceKey, Lock] = {}

    @property
    def repository(self) -> EnvironmentRepository:
        return self._repository

    async def availability(self, session_id: str) -> EnvironmentAvailability:
        return await self._repository.availability(session_id)

    async def resources(self, session_id: str) -> tuple[SessionEnvironmentResource, ...]:
        return (await self._repository.availability(session_id)).resources

    async def resource(self, session_id: str, mount_name: str) -> SessionEnvironmentResource:
        return await self._repository.resource(session_id, mount_name)

    @staticmethod
    def provider_state_ref(resource: SessionEnvironmentResource) -> ObjectRef | None:
        digest = resource.provider_state_digest
        if digest is None:
            return None
        return ObjectRef(
            object_kind=ObjectKind.provider_state,
            object_schema_version="1",
            logical_digest=digest,
        )

    @asynccontextmanager
    async def lifecycle(self, session_id: str, mount_name: str) -> AsyncGenerator[None]:
        """Serialize one Host's commands for a Session mount."""

        async with self._locks.setdefault((session_id, mount_name), Lock()):
            yield

    async def persist_runner_state(
        self,
        *,
        session_id: str,
        mount_name: str,
        provider_key: str,
        provider_spec_digest: str,
        state_version: str | None,
        provider_state: JsonValue | None,
        status: EnvironmentResourceStatus,
    ) -> SessionEnvironmentResource:
        """Publish and select one detached state transition returned by a Runner."""

        current = await self._repository.resource(session_id, mount_name)
        if current.provider_key != provider_key or current.provider_spec_digest != provider_spec_digest:
            raise StoreIntegrityError(
                "A Runner provider-state update does not match the selected resource.",
                code="provider_state_reference_mismatch",
            )
        if provider_state is None:
            if state_version is not None:
                raise EnvironmentLifecycleError(
                    "A cleared provider state cannot retain a state version.",
                    code="provider_state_invalid",
                )
            digest = None
        else:
            state = EnvironmentProviderResourceState.model_validate(provider_state, strict=True)
            if state.provider_key != provider_key or state.state_version != state_version:
                raise EnvironmentLifecycleError(
                    "A Runner provider state does not match its transition metadata.",
                    code="provider_state_invalid",
                )
            stored = StoredProviderState(
                session_id=session_id,
                mount_name=mount_name,
                provider_key=provider_key,
                provider_spec_digest=provider_spec_digest,
                state_version=state.state_version,
                provider_state=state.model_dump(mode="json"),
                exported_at=datetime.now(UTC),
            )
            reference = await self._store.publish_object(
                object_kind=ObjectKind.provider_state,
                object_schema_version="1",
                payload=stored.model_dump(mode="json"),
            )
            digest = reference.logical_digest
        return await self._repository.save(
            session_id,
            mount_name,
            status=status,
            provider_state_digest=digest,
        )


__all__ = ["EnvironmentService"]
