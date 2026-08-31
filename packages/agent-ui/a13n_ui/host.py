"""Stable surface-neutral Agent UI Host composition root."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from a13n_environment_provider import EnvironmentPauseMode
from a13n_harness import RunInputValue
from anyio import CancelScope, Event, Lock, move_on_after
from anyio.streams.memory import MemoryObjectReceiveStream
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai.usage import RunUsage

from a13n_ui.composition import (
    AgentEnvironmentCompatibility,
    CompositionService,
    ResolvedAgentSnapshot,
    ResolvedEnvironmentSnapshot,
    SnapshotReference,
)
from a13n_ui.configuration import (
    ConfigurationDiagnostic,
    ConfigurationGeneration,
    ConfigurationService,
    LocalSkillDiscoverySettings,
    LocalSkillSourceDefinition,
    ResourceRevision,
    ResourceRevisionRef,
    SkillChangePreview,
    SkillConflictPreview,
    SkillScanPreview,
    SkillSourceStatus,
)
from a13n_ui.configuration.catalog import CatalogRepository
from a13n_ui.environments import EnvironmentAvailability, EnvironmentService
from a13n_ui.errors import EnvironmentLifecycleError, HostStateError
from a13n_ui.runs import ForegroundRunCoordinator
from a13n_ui.runtime_generations import (
    RuntimeGenerationService,
    RuntimeRestartResult,
    RuntimeStatus,
)
from a13n_ui.runtime_generations.wire import ExecuteEnvironmentCommand, ProviderStateUpdate, SelectedEnvironmentResource
from a13n_ui.sessions import (
    EventSubscription,
    LocalSession,
    SessionAgentSkillSelection,
    SessionEventHub,
    SessionRunResult,
    SessionService,
    SessionSummary,
    SessionUpdate,
)
from a13n_ui.settings import AgentUiSettings
from a13n_ui.storage import LocalStore, ObjectKind, ObjectRef, open_local_store


class HostState(StrEnum):
    """Observable lifecycle state for one stable Host instance."""

    starting = "starting"
    ready = "ready"
    stopping = "stopping"
    closed = "closed"


class HostStatus(BaseModel):
    """Detached safe Host health projection."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    state: HostState
    process_generation: str = Field(min_length=1, max_length=64)
    object_count: int = Field(ge=0)


class AgentUiHost:
    """The only product boundary shared by Agent UI presentation surfaces."""

    def __init__(
        self,
        settings: AgentUiSettings,
        store: LocalStore,
    ) -> None:
        self._settings = settings
        self._store = store
        self._state = HostState.starting
        catalog = CatalogRepository(store)
        self._configuration = ConfigurationService(settings.configuration, catalog)
        self._composition = CompositionService(store, catalog)
        self._environments = EnvironmentService(store=store)
        self._sessions = SessionService(store, self._composition)
        self._events = SessionEventHub()
        self._runtime = RuntimeGenerationService(
            settings.runtime,
            storage=settings.storage,
            configuration=settings.configuration,
            envd_runtime=settings.envd_runtime,
        )
        self._runs = ForegroundRunCoordinator(
            sessions=self._sessions,
            composition=self._composition,
            environments=self._environments,
            events=self._events,
            runtime=self._runtime,
        )
        self._operation_lock = Lock()
        self._operation_scopes: set[CancelScope] = set()
        self._operations_idle = Event()
        self._operations_idle.set()

    @property
    def state(self) -> HostState:
        return self._state

    async def status(self) -> HostStatus:
        async with self._operation():
            return HostStatus(
                state=self._state,
                process_generation=self._store.process_generation,
                object_count=await self._store.object_count(),
            )

    async def runtime_status(self) -> RuntimeStatus:
        async with self._operation():
            return await self._runtime.status()

    async def restart_runtime(self) -> RuntimeRestartResult:
        async with self._operation():
            return await self._runtime.restart()

    async def current_configuration(self) -> ConfigurationGeneration | None:
        async with self._operation():
            return await self._configuration.current_generation()

    async def retained_configurations(self, *, limit: int = 100) -> tuple[ConfigurationGeneration, ...]:
        async with self._operation():
            return await self._configuration.retained_generations(limit=limit)

    async def configuration_resource(self, reference: ResourceRevisionRef) -> ResourceRevision:
        async with self._operation():
            return await self._configuration.resource(reference)

    async def configuration_diagnostics(self, *, limit: int = 100) -> tuple[ConfigurationDiagnostic, ...]:
        async with self._operation():
            return await self._configuration.diagnostics(limit=limit)

    async def reload_configuration(self) -> ConfigurationGeneration:
        async with self._operation():
            return await self._configuration.reload()

    async def resolve_agent_snapshot(
        self,
        agent_id: str,
        *,
        generation_id: str | None = None,
    ) -> SnapshotReference:
        async with self._operation():
            return await self._composition.resolve_agent(
                agent_id,
                generation_id=generation_id,
            )

    async def resolve_environment_snapshot(
        self,
        environment_id: str,
        *,
        generation_id: str | None = None,
    ) -> SnapshotReference:
        async with self._operation():
            return await self._composition.resolve_environment(
                environment_id,
                generation_id=generation_id,
            )

    async def agent_snapshot(self, reference: SnapshotReference) -> ResolvedAgentSnapshot:
        async with self._operation():
            return await self._composition.agent(reference)

    async def environment_snapshot(
        self,
        reference: SnapshotReference,
    ) -> ResolvedEnvironmentSnapshot:
        async with self._operation():
            return await self._composition.environment(reference)

    async def validate_agent_environment(
        self,
        agent_reference: SnapshotReference,
        environment_reference: SnapshotReference,
    ) -> AgentEnvironmentCompatibility:
        async with self._operation():
            return await self._composition.compatibility(
                agent_reference,
                environment_reference,
            )

    async def validate_agent_executable(
        self,
        reference: SnapshotReference,
        environment_reference: SnapshotReference | None = None,
    ) -> None:
        async with self._operation():
            await self._composition.validate_executable(
                reference,
                environment_reference,
            )

    async def create_session(
        self,
        *,
        agent_snapshot: SnapshotReference,
        environment_snapshot: SnapshotReference,
        title: str | None = None,
        skill_selections: tuple[SessionAgentSkillSelection, ...] = (),
    ) -> LocalSession:
        async with self._operation():
            session = await self._sessions.create(
                agent_snapshot=agent_snapshot,
                environment_snapshot=environment_snapshot,
                title=title,
                skill_selections=skill_selections,
            )
            return await self._provision_eager_environment(session)

    async def fork_session(
        self,
        source_session_id: str,
        *,
        agent_snapshot: SnapshotReference | None = None,
        environment_snapshot: SnapshotReference | None = None,
        title: str | None = None,
        skill_selections: tuple[SessionAgentSkillSelection, ...] | None = None,
    ) -> LocalSession:
        async with self._operation():
            session = await self._sessions.fork(
                source_session_id,
                agent_snapshot=agent_snapshot,
                environment_snapshot=environment_snapshot,
                title=title,
                skill_selections=skill_selections,
            )
            return await self._provision_eager_environment(session)

    async def session(self, session_id: str) -> LocalSession:
        async with self._operation():
            return await self._sessions.get(session_id)

    async def sessions(
        self,
        *,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[SessionSummary, ...]:
        async with self._operation():
            return await self._sessions.list(include_archived=include_archived, limit=limit)

    async def update_session(
        self,
        session_id: str,
        *,
        update: SessionUpdate,
    ) -> LocalSession:
        async with self._operation():
            return await self._sessions.update(session_id, update=update)

    async def session_environment(self, session_id: str) -> EnvironmentAvailability:
        async with self._operation():
            return await self._environments.availability(session_id)

    async def provision_session_environment(self, session_id: str) -> EnvironmentAvailability:
        async with self._operation():
            for resource in await self._environments.resources(session_id):
                await self._execute_environment_command(
                    session_id,
                    resource.mount_name,
                    action="ensure_available",
                )
            return await self._environments.availability(session_id)

    async def retry_session_environment(
        self,
        session_id: str,
        mount_name: str,
    ) -> EnvironmentAvailability:
        async with self._operation():
            await self._execute_environment_command(session_id, mount_name, action="ensure_available")
            return await self._environments.availability(session_id)

    async def pause_session_environment(
        self,
        session_id: str,
        mount_name: str,
        *,
        mode: EnvironmentPauseMode = EnvironmentPauseMode.FILESYSTEM,
    ) -> EnvironmentAvailability:
        async with self._operation():
            await self._execute_environment_command(session_id, mount_name, action="pause", pause_mode=mode)
            return await self._environments.availability(session_id)

    async def destroy_session_environment(
        self,
        session_id: str,
        mount_name: str,
    ) -> EnvironmentAvailability:
        async with self._operation():
            await self._execute_environment_command(session_id, mount_name, action="destroy")
            return await self._environments.availability(session_id)

    async def run_session(
        self,
        session_id: str,
        *,
        input_value: RunInputValue,
    ) -> SessionRunResult:
        async with self._operation():
            return await self._runs.run(session_id=session_id, input_value=input_value)

    async def resume_session(
        self,
        session_id: str,
        *,
        results: DeferredToolResults,
    ) -> SessionRunResult:
        async with self._operation():
            return await self._runs.resume(session_id=session_id, results=results)

    async def session_deferred_requests(self, session_id: str) -> DeferredToolRequests:
        async with self._operation():
            return await self._runs.deferred_requests(session_id)

    async def session_async_usage(self, session_id: str) -> RunUsage:
        """Return generation-memory usage from deduplicated asynchronous work."""

        async with self._operation():
            await self._sessions.get(session_id)
            return await self._runs.async_usage(session_id)

    async def cancel_session(self, session_id: str) -> bool:
        async with self._operation():
            return await self._runs.cancel(session_id)

    @asynccontextmanager
    async def subscribe_session_events(
        self,
        session_id: str,
        *,
        capacity: int = 256,
    ) -> AsyncGenerator[EventSubscription]:
        async with self._operation():
            async with self._events.subscribe(session_id, capacity=capacity) as subscription:
                yield subscription

    async def delete_session(self, session_id: str) -> None:
        async with self._operation():
            await self._sessions.get(session_id)
            await self._runs.cancel(session_id)
            cleanup_error: Exception | None = None
            for resource in await self._environments.resources(session_id):
                try:
                    await self._execute_environment_command(
                        session_id,
                        resource.mount_name,
                        action="destroy",
                    )
                except Exception as exc:
                    cleanup_error = exc
            await self._sessions.delete(session_id)
            if cleanup_error is not None:
                raise EnvironmentLifecycleError(
                    "The Session was deleted, but one or more Environment resources could not be destroyed.",
                    code="session_deleted_cleanup_failed",
                ) from cleanup_error

    async def skill_source_statuses(self) -> tuple[SkillSourceStatus, ...]:
        async with self._operation():
            return await self._configuration.skill_source_statuses()

    async def upsert_skill_source(
        self,
        source: LocalSkillSourceDefinition,
        *,
        target_root_id: str,
    ) -> ConfigurationGeneration:
        async with self._operation():
            return await self._configuration.upsert_skill_source(
                source,
                target_root_id=target_root_id,
            )

    async def delete_skill_source(self, skill_source_id: str) -> ConfigurationGeneration:
        async with self._operation():
            return await self._configuration.delete_skill_source(skill_source_id)

    async def reorder_skill_sources(
        self,
        skill_source_ids: tuple[str, ...],
    ) -> ConfigurationGeneration:
        async with self._operation():
            return await self._configuration.reorder_skill_sources(skill_source_ids)

    async def skill_conflicts(
        self,
        discovery: LocalSkillDiscoverySettings | None = None,
    ) -> SkillConflictPreview:
        async with self._operation():
            return await self._configuration.skill_conflicts(discovery)

    async def scan_skill_source(self, skill_source_id: str) -> SkillScanPreview:
        async with self._operation():
            return await self._configuration.scan_skill_source(skill_source_id)

    async def scan_skills(
        self,
        discovery: LocalSkillDiscoverySettings | None = None,
    ) -> SkillScanPreview:
        async with self._operation():
            return await self._configuration.scan_skills(discovery)

    async def discard_skill_scan(self, scan_id: str) -> None:
        async with self._operation():
            await self._configuration.discard_skill_scan(scan_id)

    async def preview_skill_import(
        self,
        *,
        scan_id: str,
        skill_name: str,
        skill_id: str,
        display_name: str,
        target_root_id: str,
    ) -> SkillChangePreview:
        async with self._operation():
            return await self._configuration.preview_skill_import(
                scan_id=scan_id,
                skill_name=skill_name,
                skill_id=skill_id,
                display_name=display_name,
                target_root_id=target_root_id,
            )

    async def preview_skill_refresh(
        self,
        *,
        scan_id: str,
        skill_name: str,
        skill_id: str,
    ) -> SkillChangePreview:
        async with self._operation():
            return await self._configuration.preview_skill_refresh(
                scan_id=scan_id,
                skill_name=skill_name,
                skill_id=skill_id,
            )

    async def accept_skill_import(self, change_id: str) -> ConfigurationGeneration:
        async with self._operation():
            return await self._configuration.accept_skill_import(change_id)

    async def accept_skill_refresh(self, change_id: str) -> ConfigurationGeneration:
        async with self._operation():
            return await self._configuration.accept_skill_refresh(change_id)

    async def discard_skill_change(self, change_id: str) -> None:
        async with self._operation():
            await self._configuration.discard_skill_change(change_id)

    @asynccontextmanager
    async def subscribe_configurations(
        self,
        *,
        capacity: int = 16,
    ) -> AsyncGenerator[MemoryObjectReceiveStream[ConfigurationGeneration]]:
        async with self._operation():
            async with self._configuration.subscribe(capacity=capacity) as subscription:
                yield subscription

    @asynccontextmanager
    async def _operation(self) -> AsyncGenerator[None]:
        scope = CancelScope()
        async with self._operation_lock:
            self._require_ready()
            if not self._operation_scopes:
                self._operations_idle = Event()
            self._operation_scopes.add(scope)
        cancelled_by_shutdown = False
        try:
            with scope:
                yield
            cancelled_by_shutdown = scope.cancel_called
        finally:
            with CancelScope(shield=True):
                async with self._operation_lock:
                    self._operation_scopes.discard(scope)
                    if not self._operation_scopes:
                        self._operations_idle.set()
        if cancelled_by_shutdown:
            raise HostStateError(
                "Agent UI operation was cancelled during Host shutdown.",
                code="host_stopping",
            )

    async def _provision_eager_environment(self, session: LocalSession) -> LocalSession:
        snapshot = await self._composition.environment(session.environment_snapshot)
        if snapshot.definition.lifecycle.provision == "eager":
            for resource in await self._environments.resources(session.session_id):
                await self._execute_environment_command(
                    session.session_id,
                    resource.mount_name,
                    action="ensure_available",
                )
        return session

    async def _execute_environment_command(
        self,
        session_id: str,
        mount_name: str,
        *,
        action: Literal["ensure_available", "pause", "destroy"],
        pause_mode: EnvironmentPauseMode | None = None,
    ) -> None:
        session = await self._sessions.get(session_id)
        async with self._environments.lifecycle(session_id, mount_name):
            resource = await self._environments.resource(session_id, mount_name)
            request = ExecuteEnvironmentCommand(
                request_id=f"request-{uuid4().hex}",
                generation_id="runtime-pending",
                session_id=session_id,
                environment_snapshot=ObjectRef(
                    object_kind=ObjectKind.environment_snapshot,
                    object_schema_version="1",
                    logical_digest=session.environment_snapshot.object_digest,
                ),
                selected_resource=SelectedEnvironmentResource(
                    resource=resource,
                    provider_state=self._environments.provider_state_ref(resource),
                ),
                action=action,
                pause_mode=pause_mode,
            )
            result = await self._runtime.execute_environment(
                request,
                on_provider_state=self._persist_provider_state,
            )
        if result.failure is not None:
            raise EnvironmentLifecycleError(
                "The runtime Runner could not complete the Environment operation.",
                code="environment_operation_failed",
                details={"failure": result.failure},
            )

    async def _persist_provider_state(self, update: ProviderStateUpdate) -> None:
        await self._environments.persist_runner_state(
            session_id=update.session_id,
            mount_name=update.mount_name,
            provider_key=update.provider_key,
            provider_spec_digest=update.provider_spec_digest,
            state_version=update.state_version,
            provider_state=update.provider_state,
            status=update.status,
        )

    async def _stop(self) -> None:
        async with self._operation_lock:
            if self._state is not HostState.ready:
                return
            self._state = HostState.stopping
            idle = self._operations_idle

        await self._runs.cancel_all()
        with move_on_after(self._settings.shutdown_timeout_seconds) as drain_scope:
            await idle.wait()
        if not drain_scope.cancel_called:
            return

        async with self._operation_lock:
            scopes = tuple(self._operation_scopes)
            idle = self._operations_idle
        for scope in scopes:
            scope.cancel()
        # Accepted operations retain process-local collaborators until their
        # cancellation cleanup completes, so shutdown waits for that ownership
        # boundary before closing the services they use.
        await idle.wait()

    def _require_ready(self) -> None:
        if self._state is not HostState.ready:
            raise HostStateError(
                "Agent UI Host is not accepting commands.",
                code="host_not_ready",
                details={"state": self._state.value},
            )


@asynccontextmanager
async def open_agent_ui_host(
    settings: AgentUiSettings,
) -> AsyncGenerator[AgentUiHost]:
    """Start, expose, and close one complete stable Agent UI Host lifetime."""

    host: AgentUiHost | None = None
    try:
        async with open_local_store(settings.storage) as store:
            host = AgentUiHost(settings, store)
            try:
                await host._configuration.initialize()
                await host._runtime.start()
                host._state = HostState.ready
                yield host
            finally:
                with CancelScope(shield=True):
                    await host._stop()
                    try:
                        await host._runs.close()
                        await host._runtime.close()
                    finally:
                        await host._composition.close()
                    await host._configuration.close()
                    host._state = HostState.closed
    finally:
        if host is not None:
            host._state = HostState.closed


__all__ = ["AgentUiHost", "HostState", "HostStatus", "open_agent_ui_host"]
