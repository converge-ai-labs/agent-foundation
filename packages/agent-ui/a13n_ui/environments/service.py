"""Process-local provider lifecycle and Harness runtime service for Session Environments."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from a13n_environment_provider import (
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    EnvironmentProvider,
    EnvironmentProviderError,
    EnvironmentProviderFactoryCatalog,
    EnvironmentProviderResourceState,
    EnvironmentProviderSpec,
    EnvironmentResource,
    EnvironmentRuntimeAttachment,
)
from a13n_harness.environment import ENVIRONMENT_ACTION_DISPATCH, EnvironmentAction, EnvironmentPermissionSet
from a13n_harness.environment.advanced import (
    EnvironmentRuntime,
    EnvironmentRuntimeMount,
    create_environment_provider_binding,
    create_environment_runtime,
)
from anyio import Event, Lock
from pydantic import JsonValue, ValidationError

from a13n_ui.composition import ResolvedEnvironmentMountDefinition, ResolvedEnvironmentSnapshot
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
from .runtime import ProviderRuntimeResolver

_ResourceKey = tuple[str, str]


@dataclass(slots=True)
class _LiveResource:
    provider: EnvironmentProvider
    resource: EnvironmentResource


class EnvironmentService:
    """Persist latest provider state and lend fresh runtime attachments."""

    def __init__(
        self,
        *,
        store: LocalStore,
        factories: EnvironmentProviderFactoryCatalog,
        runtimes: ProviderRuntimeResolver,
    ) -> None:
        self._store = store
        self._repository = EnvironmentRepository(store)
        self._factories = factories
        self._runtimes = runtimes
        self._locks: dict[_ResourceKey, Lock] = {}
        self._live: dict[_ResourceKey, _LiveResource] = {}
        self._borrow_counts: dict[_ResourceKey, int] = {}
        self._borrow_zero: dict[_ResourceKey, Event] = {}
        self._lifecycle_pending: set[_ResourceKey] = set()

    @property
    def repository(self) -> EnvironmentRepository:
        return self._repository

    async def availability(self, session_id: str) -> EnvironmentAvailability:
        return await self._repository.availability(session_id)

    async def provision(self, session_id: str) -> EnvironmentAvailability:
        availability = await self._repository.availability(session_id)
        for resource in availability.resources:
            await self._ensure_available(resource.session_id, resource.mount_name)
        return await self._repository.availability(session_id)

    @asynccontextmanager
    async def run_environment(
        self,
        *,
        session_id: str,
        snapshot: ResolvedEnvironmentSnapshot,
    ) -> AsyncGenerator[EnvironmentRuntime]:
        availability = await self.provision(session_id)
        resource_by_name = {item.mount_name: item for item in availability.resources}
        snapshot_by_name = {item.mount_name: item for item in snapshot.mounts}
        if set(resource_by_name) != set(snapshot_by_name):
            raise StoreIntegrityError(
                "Session Environment resources differ from the pinned desired mounts.",
                code="environment_resource_mismatch",
            )
        runtime_mounts: dict[str, EnvironmentRuntimeMount] = {}
        async with AsyncExitStack() as attachments:
            for mount in snapshot.mounts:
                attachment = await attachments.enter_async_context(
                    self._borrow_attachment((session_id, mount.mount_name))
                )
                runtime_mounts[mount.model_alias] = EnvironmentRuntimeMount(
                    binding=create_environment_provider_binding(attachment),
                    permission_ceiling=_permissions(mount),
                    working_directory="/",
                )
            desired_default = snapshot.definition.default_mount
            default_mount = snapshot_by_name[desired_default].model_alias if desired_default is not None else None
            yield create_environment_runtime(
                mounts=runtime_mounts,
                default_mount=default_mount,
            )

    async def apply_idle_policy(self, session_id: str, snapshot: ResolvedEnvironmentSnapshot) -> None:
        if snapshot.definition.lifecycle.idle == "keep_running":
            return
        for resource in (await self._repository.availability(session_id)).resources:
            if resource.status is EnvironmentResourceStatus.available:
                await self.pause(
                    resource.session_id,
                    resource.mount_name,
                    mode=EnvironmentPauseMode.FULL,
                )

    async def release_session(self, session_id: str) -> None:
        failures: list[BaseException] = []
        for resource in (await self._repository.availability(session_id)).resources:
            try:
                await self.destroy(resource.session_id, resource.mount_name)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise BaseExceptionGroup("Environment cleanup failed", failures)

    async def retry(self, session_id: str, mount_name: str) -> SessionEnvironmentResource:
        return await self._ensure_available(session_id, mount_name)

    async def pause(
        self,
        session_id: str,
        mount_name: str,
        *,
        mode: EnvironmentPauseMode,
    ) -> SessionEnvironmentResource:
        key = (session_id, mount_name)
        async with self._exclusive_lifecycle(key):
            current = await self._repository.resource(*key)
            if current.status is EnvironmentResourceStatus.paused:
                return current
            await self._ensure_available_locked(current)
            live = self._live[key]
            operation = _operation(EnvironmentManagementAction.PAUSE, key)
            try:
                state = await live.provider.pause(live.resource, operation=operation, mode=mode)
                await live.resource.__aexit__(None, None, None)
                self._live.pop(key, None)
                digest = await self._publish_state(current, state)
                return await self._repository.save(
                    *key,
                    status=EnvironmentResourceStatus.paused,
                    provider_state_digest=digest,
                )
            except BaseException as exc:
                await self._mark_unavailable(current, exc)
                raise

    async def destroy(self, session_id: str, mount_name: str) -> SessionEnvironmentResource:
        key = (session_id, mount_name)
        async with self._exclusive_lifecycle(key):
            current = await self._repository.resource(*key)
            live = self._live.pop(key, None)
            if live is not None:
                await live.resource.__aexit__(None, None, None)
            state = await self._load_state(current)
            if state is None:
                return await self._repository.save(
                    *key,
                    status=EnvironmentResourceStatus.unprovisioned,
                    provider_state_digest=None,
                )
            try:
                provider = await self._provider(current)
                await provider.destroy(state, operation=_operation(EnvironmentManagementAction.DESTROY, key))
                return await self._repository.save(
                    *key,
                    status=EnvironmentResourceStatus.unprovisioned,
                    provider_state_digest=None,
                )
            except BaseException as exc:
                await self._mark_unavailable(current, exc)
                raise

    async def close(self) -> None:
        live = tuple(self._live.values())
        self._live.clear()
        failures: list[BaseException] = []
        for item in reversed(live):
            try:
                await item.resource.__aexit__(None, None, None)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise BaseExceptionGroup("Environment resource shutdown failed", failures)

    async def _ensure_available(self, session_id: str, mount_name: str) -> SessionEnvironmentResource:
        key = (session_id, mount_name)
        async with self._lock_for(key):
            return await self._ensure_available_locked(await self._repository.resource(*key))

    async def _ensure_available_locked(
        self,
        current: SessionEnvironmentResource,
    ) -> SessionEnvironmentResource:
        key = (current.session_id, current.mount_name)
        live = self._live.get(key)
        if live is not None and live.resource.is_entered:
            return current
        state = await self._load_state(current)
        create = state is None
        operation = _operation(
            EnvironmentManagementAction.CREATE if create else EnvironmentManagementAction.RESUME,
            key,
        )
        resource: EnvironmentResource | None = None
        try:
            provider = await self._provider(current)
            resource = (
                await provider.create(operation=operation)
                if create
                else await provider.resume(state, operation=operation)
            )
            await resource.__aenter__()
            digest = await self._publish_state(current, resource.state)
            selected = await self._repository.save(
                *key,
                status=EnvironmentResourceStatus.available,
                provider_state_digest=digest,
            )
            self._live[key] = _LiveResource(provider=provider, resource=resource)
            return selected
        except BaseException as exc:
            if resource is not None and resource.is_entered:
                try:
                    await resource.__aexit__(type(exc), exc, exc.__traceback__)
                except BaseException:
                    pass
            await self._mark_unavailable(current, exc)
            raise

    async def _provider(self, resource: SessionEnvironmentResource) -> EnvironmentProvider:
        provider_key, schema_version, digest, parameters = await self._repository.provider_spec(
            resource.session_id,
            resource.mount_name,
        )
        if provider_key != resource.provider_key or digest != resource.provider_spec_digest:
            raise StoreIntegrityError(
                "Environment resource provider identity changed.",
                code="environment_resource_identity_conflict",
            )
        runtime = await self._runtimes.resolve(provider_key)
        return self._factories.create_provider(
            EnvironmentProviderSpec(
                provider_key=provider_key,
                schema_version=schema_version,
                parameters=parameters,
            ),
            runtime=runtime,
        )

    async def _publish_state(
        self,
        resource: SessionEnvironmentResource,
        state: EnvironmentProviderResourceState,
    ) -> str:
        if state.provider_key != resource.provider_key:
            raise EnvironmentLifecycleError(
                "Provider state names another provider.",
                code="provider_state_invalid",
            )
        stored = StoredProviderState(
            session_id=resource.session_id,
            mount_name=resource.mount_name,
            provider_key=resource.provider_key,
            provider_spec_digest=resource.provider_spec_digest,
            state_version=state.state_version,
            provider_state=state.model_dump(mode="json"),
            exported_at=datetime.now(UTC),
        )
        reference = await self._store.publish_object(
            object_kind=ObjectKind.provider_state,
            object_schema_version="1",
            payload=stored.model_dump(mode="json"),
        )
        return reference.logical_digest

    async def _load_state(
        self,
        resource: SessionEnvironmentResource,
    ) -> EnvironmentProviderResourceState | None:
        digest = resource.provider_state_digest
        if digest is None:
            return None
        envelope = await self._store.read_object(
            ObjectRef(
                object_kind=ObjectKind.provider_state,
                object_schema_version="1",
                logical_digest=digest,
            )
        )
        try:
            stored = StoredProviderState.model_validate_json(json.dumps(envelope.payload))
            state = EnvironmentProviderResourceState.model_validate(stored.provider_state)
        except ValidationError as exc:
            raise StoreIntegrityError(
                "A selected Environment provider state object is invalid.",
                code="provider_state_invalid",
            ) from exc
        if (
            stored.session_id != resource.session_id
            or stored.mount_name != resource.mount_name
            or stored.provider_key != resource.provider_key
            or stored.provider_spec_digest != resource.provider_spec_digest
            or state.provider_key != resource.provider_key
            or state.state_version != stored.state_version
        ):
            raise StoreIntegrityError(
                "A selected Environment provider state does not match its resource.",
                code="provider_state_reference_mismatch",
            )
        return state

    async def _mark_unavailable(
        self,
        resource: SessionEnvironmentResource,
        error: BaseException,
    ) -> None:
        failure: JsonValue = {"code": "environment_operation_failed"}
        if isinstance(error, EnvironmentProviderError):
            failure = error.safe_projection().model_dump(mode="json")
        await self._repository.save(
            resource.session_id,
            resource.mount_name,
            status=EnvironmentResourceStatus.unavailable,
            provider_state_digest=resource.provider_state_digest,
            failure=failure,
        )

    @asynccontextmanager
    async def _borrow_attachment(
        self,
        key: _ResourceKey,
    ) -> AsyncGenerator[EnvironmentRuntimeAttachment]:
        lock = self._lock_for(key)
        async with lock:
            if key in self._lifecycle_pending:
                raise EnvironmentLifecycleError(
                    "The Environment resource is entering a lifecycle operation.",
                    code="environment_lifecycle_pending",
                )
            live = self._live.get(key)
            if live is None or not live.resource.is_entered:
                raise EnvironmentLifecycleError(
                    "An available Environment resource has no entered provider scope.",
                    code="environment_resource_disconnected",
                )
            count = self._borrow_counts.get(key, 0)
            if count == 0:
                self._borrow_zero[key] = Event()
            self._borrow_counts[key] = count + 1
        attachment_context = live.resource.acquire_attachment()
        try:
            async with attachment_context as attachment:
                yield attachment
        finally:
            async with lock:
                remaining = self._borrow_counts[key] - 1
                if remaining == 0:
                    self._borrow_counts.pop(key, None)
                    self._borrow_zero[key].set()
                else:
                    self._borrow_counts[key] = remaining

    @asynccontextmanager
    async def _exclusive_lifecycle(self, key: _ResourceKey) -> AsyncGenerator[None]:
        lock = self._lock_for(key)
        async with lock:
            if key in self._lifecycle_pending:
                raise EnvironmentLifecycleError(
                    "The Environment resource already has a lifecycle operation.",
                    code="environment_lifecycle_pending",
                )
            self._lifecycle_pending.add(key)
            zero = self._borrow_zero.get(key)
            if zero is None or self._borrow_counts.get(key, 0) == 0:
                zero = Event()
                zero.set()
                self._borrow_zero[key] = zero
        try:
            await zero.wait()
            async with lock:
                yield
        finally:
            async with lock:
                self._lifecycle_pending.discard(key)

    def _lock_for(self, key: _ResourceKey) -> Lock:
        return self._locks.setdefault(key, Lock())


def _operation(action: EnvironmentManagementAction, key: _ResourceKey) -> EnvironmentOperationContext:
    return EnvironmentOperationContext(
        operation_id=f"operation-{uuid4().hex}",
        action=action,
        resource_correlation=f"{key[0]}:{key[1]}",
        attempt=1,
    )


def _permissions(mount: ResolvedEnvironmentMountDefinition) -> EnvironmentPermissionSet:
    operations: set[EnvironmentAction] = set()
    for value in mount.permission_ceiling:
        family_matches = {
            action for action, dispatch in ENVIRONMENT_ACTION_DISPATCH.items() if dispatch.family == value
        }
        if family_matches:
            operations.update(family_matches)
            continue
        try:
            operations.add(EnvironmentAction(value))
        except ValueError as exc:
            raise StoreIntegrityError(
                "A pinned Environment permission ceiling is invalid.",
                code="environment_permission_invalid",
            ) from exc
    return EnvironmentPermissionSet(operations=frozenset(operations))


__all__ = ["EnvironmentService"]
