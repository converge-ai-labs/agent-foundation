"""Host-owned lifecycle and Harness runtime service for Session Environments."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from a13n_environment_provider import (
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    EnvironmentProvider,
    EnvironmentProviderError,
    EnvironmentProviderFactoryCatalog,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderResourceState,
    EnvironmentProviderSpec,
    EnvironmentReconciliationPhase,
    EnvironmentResource,
    EnvironmentRuntimeAttachment,
)
from a13n_harness.environment import (
    ENVIRONMENT_ACTION_DISPATCH,
    EnvironmentAction,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.advanced import (
    EnvironmentRuntime,
    EnvironmentRuntimeLimits,
    EnvironmentRuntimeMount,
    EnvironmentStateLimits,
    create_environment_provider_binding,
    create_environment_runtime,
)
from anyio import CancelScope, Event, Lock
from pydantic import ValidationError

from a13n_ui.composition import ResolvedEnvironmentMountDefinition, ResolvedEnvironmentSnapshot
from a13n_ui.errors import AgentUiError, EnvironmentLifecycleError, RuntimeResolutionError, StoreIntegrityError
from a13n_ui.storage.objects import ObjectKind, ObjectRef
from a13n_ui.storage.runtime import LocalStore

from .models import (
    EnvironmentAvailability,
    HostEnvironmentResource,
    HostResourceLifecycleState,
    StoredProviderState,
)
from .repository import EnvironmentRepository
from .runtime import ProviderRuntimeResolver


@dataclass(slots=True)
class _LiveResource:
    provider: EnvironmentProvider
    resource: EnvironmentResource


class EnvironmentService:
    """Fence provider effects and lend fresh single-use Environment runtimes."""

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
        self._locks_guard = Lock()
        self._resource_locks: dict[str, Lock] = {}
        self._live: dict[str, _LiveResource] = {}
        self._borrow_counts: dict[str, int] = {}
        self._borrow_zero: dict[str, Event] = {}
        self._lifecycle_pending: set[str] = set()

    @property
    def repository(self) -> EnvironmentRepository:
        return self._repository

    async def initialize(self) -> int:
        """Validate selected authority and reconcile interrupted provider operations."""

        invalid_resources: set[str] = set()
        state_required = {
            HostResourceLifecycleState.available,
            HostResourceLifecycleState.paused,
            HostResourceLifecycleState.pausing,
            HostResourceLifecycleState.resuming,
            HostResourceLifecycleState.destroying,
        }
        for resource in await self._repository.all_resources():
            try:
                if resource.selected_provider_state_digest is not None:
                    await self._load_selected_state(resource)
                elif resource.lifecycle_state in state_required:
                    raise StoreIntegrityError(
                        "A retained Environment resource has no selected provider state.",
                        code="provider_state_missing",
                    )
            except AgentUiError as exc:
                invalid_resources.add(resource.host_resource_id)
                await self._store.record_recovery_diagnostic(
                    code="provider_state_invalid",
                    detail=f"{resource.host_resource_id}:{exc.code}",
                )
                await self._repository.fail_closed(
                    resource.host_resource_id,
                    failure={
                        "code": "provider_state_invalid",
                        "authority_error": exc.code,
                    },
                )

        resources = await self._repository.resources_in_states(
            frozenset(
                {
                    HostResourceLifecycleState.creating,
                    HostResourceLifecycleState.resuming,
                    HostResourceLifecycleState.pausing,
                    HostResourceLifecycleState.destroying,
                    HostResourceLifecycleState.unknown,
                }
            )
        )
        reconciled = 0
        for resource in resources:
            if resource.host_resource_id in invalid_resources:
                continue
            if resource.lifecycle_state is not HostResourceLifecycleState.unknown:
                operation = resource.last_operation
                if operation is None:
                    continue
                await self._repository.fail_operation(
                    resource.host_resource_id,
                    fence=resource.operation_fence,
                    operation_id=operation.operation_id,
                    unknown=True,
                    failure={"code": "environment_operation_interrupted"},
                )
            try:
                await self.reconcile(resource.host_resource_id)
            except (EnvironmentLifecycleError, RuntimeResolutionError, EnvironmentProviderError):
                continue
            reconciled += 1
        return reconciled

    async def availability(self, session_id: str) -> EnvironmentAvailability:
        return await self._repository.availability(session_id)

    async def provision(self, session_id: str) -> EnvironmentAvailability:
        availability = await self._repository.availability(session_id)
        for assignment in availability.assignments:
            await self._ensure_available(assignment.host_resource_id)
        return await self._repository.availability(session_id)

    @asynccontextmanager
    async def run_environment(
        self,
        *,
        session_id: str,
        snapshot: ResolvedEnvironmentSnapshot,
    ) -> AsyncGenerator[EnvironmentRuntime]:
        """Keep fresh attachment scopes open through complete Harness runtime cleanup."""

        availability = await self.provision(session_id)
        assignment_by_name = {item.mount_name: item for item in availability.assignments}
        snapshot_by_name = {item.mount_name: item for item in snapshot.mounts}
        if set(assignment_by_name) != set(snapshot_by_name):
            raise StoreIntegrityError(
                "Session Environment assignments differ from the pinned desired mounts.",
                code="environment_assignment_mismatch",
            )
        runtime_mounts: dict[str, EnvironmentRuntimeMount] = {}
        async with AsyncExitStack() as attachments:
            for mount in snapshot.mounts:
                assignment = assignment_by_name[mount.mount_name]
                attachment = await attachments.enter_async_context(self._borrow_attachment(assignment.host_resource_id))
                provider_binding = create_environment_provider_binding(attachment)
                runtime_mounts[mount.model_alias] = EnvironmentRuntimeMount(
                    binding=provider_binding,
                    permission_ceiling=_permissions(mount),
                    working_directory="/",
                )
            desired_default = snapshot.definition.default_mount
            default_mount = snapshot_by_name[desired_default].model_alias if desired_default is not None else None
            environment = create_environment_runtime(
                mounts=runtime_mounts,
                default_mount=default_mount,
                runtime_limits=EnvironmentRuntimeLimits(max_mounts=max(1, len(runtime_mounts))),
                state_limits=EnvironmentStateLimits(),
            )
            yield environment

    async def apply_idle_policy(self, session_id: str, snapshot: ResolvedEnvironmentSnapshot) -> None:
        mode = snapshot.definition.lifecycle.idle
        if mode == "keep_running":
            return
        pause_mode = EnvironmentPauseMode.FULL if mode == "pause_full" else EnvironmentPauseMode.FILESYSTEM
        availability = await self._repository.availability(session_id)
        for resource in availability.resources:
            if resource.lifecycle_state is HostResourceLifecycleState.available:
                await self.pause(resource.host_resource_id, mode=pause_mode)

    async def release_session(self, session_id: str, snapshot: ResolvedEnvironmentSnapshot) -> None:
        availability = await self._repository.availability(session_id)
        local_counts = Counter(item.host_resource_id for item in availability.assignments)
        if snapshot.definition.lifecycle.release == "destroy_when_unreferenced":
            for resource_id, local_count in local_counts.items():
                if await self._repository.assignment_count(resource_id) != local_count:
                    continue
                current = await self._repository.resource(resource_id)
                if current.lifecycle_state is HostResourceLifecycleState.unknown:
                    current = await self.reconcile(resource_id)
                    if current.lifecycle_state is HostResourceLifecycleState.unknown:
                        raise EnvironmentLifecycleError(
                            "Environment cleanup reconciliation has no authoritative outcome.",
                            code="environment_cleanup_reconciliation_pending",
                        )
                await self.destroy(resource_id)
        await self._repository.detach_session_assignments(session_id)

    async def pause(self, host_resource_id: str, *, mode: EnvironmentPauseMode) -> HostEnvironmentResource:
        async with self._exclusive_lifecycle(host_resource_id):
            current = await self._repository.resource(host_resource_id)
            if current.lifecycle_state is HostResourceLifecycleState.paused:
                return current
            live = self._live.get(host_resource_id)
            if live is None:
                await self._ensure_available_locked(current)
                live = self._live.get(host_resource_id)
            assert live is not None
            selected, operation = await self._repository.begin_operation(
                host_resource_id,
                expected_fence=(await self._repository.resource(host_resource_id)).operation_fence,
                action=EnvironmentManagementAction.PAUSE,
                allowed_states=frozenset({HostResourceLifecycleState.available}),
            )
            dispatched = False
            try:
                dispatched = True
                state = await live.provider.pause(live.resource, operation=operation, mode=mode)
                await live.resource.__aexit__(None, None, None)
                self._live.pop(host_resource_id, None)
                digest = await self._publish_state(selected, operation, state)
                return await self._repository.complete_state_operation(
                    host_resource_id,
                    fence=selected.operation_fence,
                    operation_id=operation.operation_id,
                    state=HostResourceLifecycleState.paused,
                    provider_state_digest=digest,
                )
            except BaseException as exc:
                await self._record_failure(selected, operation, exc, dispatched=dispatched)
                raise

    async def destroy(self, host_resource_id: str) -> HostEnvironmentResource:
        async with self._exclusive_lifecycle(host_resource_id):
            current = await self._repository.resource(host_resource_id)
            if current.lifecycle_state in {
                HostResourceLifecycleState.destroyed,
                HostResourceLifecycleState.unprovisioned,
            }:
                return current
            state = await self._load_selected_state(current)
            live = self._live.pop(host_resource_id, None)
            if live is not None:
                await live.resource.__aexit__(None, None, None)
            if state is None and current.lifecycle_state is HostResourceLifecycleState.missing:
                return current
            if state is None:
                raise StoreIntegrityError(
                    "A destroyable Environment resource has no selected provider state.",
                    code="provider_state_missing",
                )
            selected, operation = await self._repository.begin_operation(
                host_resource_id,
                expected_fence=current.operation_fence,
                action=EnvironmentManagementAction.DESTROY,
                allowed_states=frozenset(
                    {
                        HostResourceLifecycleState.available,
                        HostResourceLifecycleState.paused,
                        HostResourceLifecycleState.failed,
                        HostResourceLifecycleState.missing,
                    }
                ),
            )
            dispatched = False
            try:
                provider = await self._provider(selected)
                dispatched = True
                await provider.destroy(state, operation=operation)
                return await self._repository.complete_absent_operation(
                    host_resource_id,
                    fence=selected.operation_fence,
                    operation_id=operation.operation_id,
                    state=HostResourceLifecycleState.destroyed,
                )
            except BaseException as exc:
                await self._record_failure(selected, operation, exc, dispatched=dispatched)
                raise

    async def reconcile(self, host_resource_id: str) -> HostEnvironmentResource:
        lock = await self._lock_for(host_resource_id)
        async with lock:
            current = await self._repository.resource(host_resource_id)
            operation_view = current.last_operation
            if operation_view is None:
                raise EnvironmentLifecycleError(
                    "The Environment resource has no operation to reconcile.",
                    code="environment_reconciliation_unavailable",
                )
            operation = EnvironmentOperationContext(
                operation_id=operation_view.operation_id,
                action=EnvironmentManagementAction(operation_view.action),
                resource_correlation=host_resource_id,
                attempt=operation_view.attempt,
            )
            provider = await self._provider(current)
            last_state = await self._load_selected_state(current)
            result = await provider.reconcile(operation, last_known_state=last_state)
            if result.operation_id != operation.operation_id:
                raise EnvironmentLifecycleError(
                    "Provider reconciliation returned another operation identity.",
                    code="environment_reconciliation_invalid",
                )
            if result.phase in {EnvironmentReconciliationPhase.RUNNING, EnvironmentReconciliationPhase.PAUSED}:
                assert result.state is not None
                digest = await self._publish_state(current, operation, result.state)
                target = (
                    HostResourceLifecycleState.available
                    if result.phase is EnvironmentReconciliationPhase.RUNNING
                    else HostResourceLifecycleState.paused
                )
                return await self._repository.complete_state_operation(
                    host_resource_id,
                    fence=current.operation_fence,
                    operation_id=operation.operation_id,
                    state=target,
                    provider_state_digest=digest,
                )
            if result.phase is EnvironmentReconciliationPhase.ABSENT:
                absent = _absent_state(operation.action)
                return await self._repository.complete_absent_operation(
                    host_resource_id,
                    fence=current.operation_fence,
                    operation_id=operation.operation_id,
                    state=absent,
                )
            return current

    async def close(self) -> None:
        """Disconnect process-local resources without changing durable provider lifecycle."""

        live = tuple(self._live.items())
        self._live.clear()
        failures: list[BaseException] = []
        for _resource_id, item in reversed(live):
            try:
                await item.resource.__aexit__(None, None, None)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise BaseExceptionGroup("Environment resource shutdown failed", failures)

    async def _ensure_available(self, host_resource_id: str) -> HostEnvironmentResource:
        lock = await self._lock_for(host_resource_id)
        async with lock:
            current = await self._repository.resource(host_resource_id)
            return await self._ensure_available_locked(current)

    async def _ensure_available_locked(self, current: HostEnvironmentResource) -> HostEnvironmentResource:
        if current.lifecycle_state is HostResourceLifecycleState.failed:
            raise EnvironmentLifecycleError(
                "The Environment resource is failed and requires an explicit absence reset.",
                code="environment_resource_failed",
            )
        if current.lifecycle_state in {
            HostResourceLifecycleState.creating,
            HostResourceLifecycleState.resuming,
            HostResourceLifecycleState.pausing,
            HostResourceLifecycleState.destroying,
            HostResourceLifecycleState.unknown,
        }:
            raise EnvironmentLifecycleError(
                "The Environment resource requires reconciliation before use.",
                code="environment_reconciliation_required",
            )
        live = self._live.get(current.host_resource_id)
        if live is not None and live.resource.is_entered:
            if current.lifecycle_state is not HostResourceLifecycleState.available:
                raise StoreIntegrityError(
                    "An entered Environment resource conflicts with its durable lifecycle state.",
                    code="environment_resource_state_conflict",
                )
            return current
        create = current.lifecycle_state in {
            HostResourceLifecycleState.unprovisioned,
            HostResourceLifecycleState.destroyed,
            HostResourceLifecycleState.missing,
        }
        action = EnvironmentManagementAction.CREATE if create else EnvironmentManagementAction.RESUME
        allowed = (
            frozenset(
                {
                    HostResourceLifecycleState.unprovisioned,
                    HostResourceLifecycleState.destroyed,
                    HostResourceLifecycleState.missing,
                }
            )
            if create
            else frozenset({HostResourceLifecycleState.available, HostResourceLifecycleState.paused})
        )
        state = None if create else await self._load_selected_state(current)
        if not create and state is None:
            raise StoreIntegrityError(
                "A resumable Environment resource has no selected provider state.",
                code="provider_state_missing",
            )
        selected, operation = await self._repository.begin_operation(
            current.host_resource_id,
            expected_fence=current.operation_fence,
            action=action,
            allowed_states=allowed,
        )
        resource: EnvironmentResource | None = None
        dispatched = False
        try:
            provider = await self._provider(selected)
            dispatched = True
            if create:
                resource = await provider.create(operation=operation)
            else:
                assert state is not None
                resource = await provider.resume(state, operation=operation)
            await resource.__aenter__()
            digest = await self._publish_state(selected, operation, resource.state)
            completed = await self._repository.complete_state_operation(
                current.host_resource_id,
                fence=selected.operation_fence,
                operation_id=operation.operation_id,
                state=HostResourceLifecycleState.available,
                provider_state_digest=digest,
            )
            self._live[current.host_resource_id] = _LiveResource(provider=provider, resource=resource)
            return completed
        except BaseException as exc:
            with CancelScope(shield=True):
                if resource is not None and resource.is_entered:
                    try:
                        await resource.__aexit__(type(exc), exc, exc.__traceback__)
                    except BaseException as cleanup_error:
                        exc.add_note(f"Environment resource cleanup also failed with {type(cleanup_error).__name__}.")
                try:
                    await self._record_failure(selected, operation, exc, dispatched=dispatched)
                except BaseException as record_error:
                    exc.add_note(
                        f"Environment lifecycle failure recording also failed with {type(record_error).__name__}."
                    )
            raise

    async def _provider(self, resource: HostEnvironmentResource) -> EnvironmentProvider:
        provider_key, schema_version, digest, parameters = await self._repository.provider_spec(
            resource.host_resource_id
        )
        if provider_key != resource.provider_key or digest != resource.provider_spec_digest:
            raise StoreIntegrityError(
                "Environment resource provider identity changed.",
                code="environment_resource_identity_conflict",
            )
        runtime = await self._runtimes.resolve(provider_key)
        spec = EnvironmentProviderSpec(
            provider_key=provider_key,
            schema_version=schema_version,
            parameters=parameters,
        )
        return self._factories.create_provider(spec, runtime=runtime)

    async def _publish_state(
        self,
        resource: HostEnvironmentResource,
        operation: EnvironmentOperationContext,
        state: EnvironmentProviderResourceState,
    ) -> str:
        if state.provider_key != resource.provider_key:
            raise EnvironmentLifecycleError(
                "Provider state names another provider.",
                code="provider_state_invalid",
            )
        stored = StoredProviderState(
            host_resource_id=resource.host_resource_id,
            provider_key=resource.provider_key,
            provider_spec_digest=resource.provider_spec_digest,
            operation_fence=resource.operation_fence,
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

    async def _load_selected_state(
        self,
        resource: HostEnvironmentResource,
    ) -> EnvironmentProviderResourceState | None:
        digest = resource.selected_provider_state_digest
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
            state = EnvironmentProviderResourceState.model_validate_json(json.dumps(stored.provider_state))
        except ValidationError as exc:
            raise StoreIntegrityError(
                "A selected Environment provider state object is invalid.",
                code="provider_state_invalid",
            ) from exc
        if (
            stored.host_resource_id != resource.host_resource_id
            or stored.provider_key != resource.provider_key
            or stored.provider_spec_digest != resource.provider_spec_digest
            or stored.operation_fence > resource.operation_fence
            or state.provider_key != resource.provider_key
            or state.state_version != stored.state_version
        ):
            raise StoreIntegrityError(
                "A selected Environment provider state does not match its resource.",
                code="provider_state_reference_mismatch",
            )
        return state

    async def _record_failure(
        self,
        resource: HostEnvironmentResource,
        operation: EnvironmentOperationContext,
        error: BaseException,
        *,
        dispatched: bool,
    ) -> None:
        unknown = dispatched or isinstance(error, asyncio.CancelledError)
        failure: object = {"code": "environment_operation_failed"}
        if isinstance(error, EnvironmentProviderError):
            unknown = error.certainty is EnvironmentProviderOutcomeCertainty.UNKNOWN
            failure = error.safe_projection().model_dump(mode="json")
        elif isinstance(error, RuntimeResolutionError):
            failure = {"code": error.code}
        with CancelScope(shield=True):
            await self._repository.fail_operation(
                resource.host_resource_id,
                fence=resource.operation_fence,
                operation_id=operation.operation_id,
                unknown=unknown,
                failure=failure,  # type: ignore[arg-type]
            )

    @asynccontextmanager
    async def _borrow_attachment(
        self,
        host_resource_id: str,
    ) -> AsyncGenerator[EnvironmentRuntimeAttachment]:
        lock = await self._lock_for(host_resource_id)
        async with lock:
            if host_resource_id in self._lifecycle_pending:
                raise EnvironmentLifecycleError(
                    "The Environment resource is entering an exclusive lifecycle operation.",
                    code="environment_lifecycle_pending",
                )
            live = self._live.get(host_resource_id)
            if live is None or not live.resource.is_entered:
                raise EnvironmentLifecycleError(
                    "An available Environment resource has no entered provider scope.",
                    code="environment_resource_disconnected",
                )
            count = self._borrow_counts.get(host_resource_id, 0)
            if count == 0:
                self._borrow_zero[host_resource_id] = Event()
            self._borrow_counts[host_resource_id] = count + 1
        attachment_context = live.resource.acquire_attachment()
        entered = False
        exit_type: type[BaseException] | None = None
        exit_value: BaseException | None = None
        try:
            attachment = await attachment_context.__aenter__()
            entered = True
            try:
                yield attachment
            except BaseException as exc:
                exit_type = type(exc)
                exit_value = exc
                raise
        finally:
            with CancelScope(shield=True):
                try:
                    if entered:
                        await attachment_context.__aexit__(
                            exit_type,
                            exit_value,
                            exit_value.__traceback__ if exit_value is not None else None,
                        )
                finally:
                    async with lock:
                        remaining = self._borrow_counts[host_resource_id] - 1
                        if remaining == 0:
                            self._borrow_counts.pop(host_resource_id, None)
                            self._borrow_zero[host_resource_id].set()
                        else:
                            self._borrow_counts[host_resource_id] = remaining

    @asynccontextmanager
    async def _exclusive_lifecycle(self, host_resource_id: str) -> AsyncGenerator[None]:
        lock = await self._lock_for(host_resource_id)
        async with lock:
            if host_resource_id in self._lifecycle_pending:
                raise EnvironmentLifecycleError(
                    "The Environment resource already has a pending lifecycle operation.",
                    code="environment_lifecycle_pending",
                )
            self._lifecycle_pending.add(host_resource_id)
            zero = self._borrow_zero.get(host_resource_id)
            if zero is None or self._borrow_counts.get(host_resource_id, 0) == 0:
                zero = Event()
                zero.set()
                self._borrow_zero[host_resource_id] = zero
        try:
            await zero.wait()
            async with lock:
                yield
        finally:
            with CancelScope(shield=True):
                async with lock:
                    self._lifecycle_pending.discard(host_resource_id)

    async def _lock_for(self, host_resource_id: str) -> Lock:
        async with self._locks_guard:
            lock = self._resource_locks.get(host_resource_id)
            if lock is None:
                lock = Lock()
                self._resource_locks[host_resource_id] = lock
            return lock


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


def _absent_state(
    action: EnvironmentManagementAction,
) -> Literal[
    HostResourceLifecycleState.unprovisioned,
    HostResourceLifecycleState.destroyed,
    HostResourceLifecycleState.missing,
]:
    if action is EnvironmentManagementAction.CREATE:
        return HostResourceLifecycleState.unprovisioned
    if action is EnvironmentManagementAction.DESTROY:
        return HostResourceLifecycleState.destroyed
    return HostResourceLifecycleState.missing


__all__ = ["EnvironmentService"]
