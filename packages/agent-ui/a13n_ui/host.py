"""Stable surface-neutral Agent UI Host composition root."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from enum import StrEnum

from a13n_environment_provider import build_environment_provider_factory_catalog
from a13n_harness import RunInputValue
from anyio import CancelScope, Event, Lock, create_task_group, move_on_after
from anyio.streams.memory import MemoryObjectReceiveStream
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

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
    SourceTransactionManifest,
)
from a13n_ui.configuration.catalog import CatalogRepository
from a13n_ui.environments import (
    EnvdExecutableResolver,
    EnvironmentAvailability,
    EnvironmentService,
    ProviderRuntimeResolver,
)
from a13n_ui.errors import HostStateError, SessionError
from a13n_ui.model_adapters import RunModelResolverFactory, unavailable_run_model_resolver_factory
from a13n_ui.runs import ForegroundRunCoordinator
from a13n_ui.runtime_generations import (
    RuntimeGenerationService,
    RuntimeRestartResult,
    RuntimeStatus,
)
from a13n_ui.sessions import (
    EventSubscription,
    LocalSession,
    PresentationDelivery,
    SessionAgentSkillSelection,
    SessionEventStore,
    SessionLifecycleState,
    SessionService,
    SessionSummary,
    SessionUpdate,
    TurnView,
)
from a13n_ui.settings import AgentUiSettings
from a13n_ui.storage import LocalStore, StoreDiagnostic, open_local_store


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
    registered_object_count: int = Field(ge=0)


class AgentUiHost:
    """The only product boundary shared by Agent UI presentation surfaces."""

    def __init__(
        self,
        settings: AgentUiSettings,
        store: LocalStore,
        *,
        model_resolver_factory: RunModelResolverFactory = unavailable_run_model_resolver_factory,
    ) -> None:
        self._settings = settings
        self._store = store
        self._state = HostState.starting
        catalog = CatalogRepository(store)
        self._configuration = ConfigurationService(settings.configuration, catalog)
        self._composition = CompositionService(store, catalog)
        factories = build_environment_provider_factory_catalog(
            builtin_keys=settings.configuration.builtin_provider_keys,
            extension_keys=settings.configuration.extension_provider_keys,
        )
        self._envd = EnvdExecutableResolver(
            layout=store.layout,
            settings=settings.envd_runtime,
            executable_override=settings.configuration.envd_executable_override,
        )
        self._environments = EnvironmentService(
            store=store,
            factories=factories,
            runtimes=ProviderRuntimeResolver(self._envd),
        )
        self._sessions = SessionService(store, self._composition)
        self._events = SessionEventStore(store)
        self._runs = ForegroundRunCoordinator(
            sessions=self._sessions,
            composition=self._composition,
            environments=self._environments,
            events=self._events,
            model_resolver_factory=model_resolver_factory,
        )
        self._runtime = RuntimeGenerationService(settings.runtime)
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
                registered_object_count=await self._store.object_count(),
            )

    async def runtime_status(self) -> RuntimeStatus:
        async with self._operation():
            return await self._runtime.status()

    async def restart_runtime(self) -> RuntimeRestartResult:
        async with self._operation():
            return await self._runtime.restart()

    async def recovery_diagnostics(self, *, limit: int = 100) -> tuple[StoreDiagnostic, ...]:
        async with self._operation():
            return await self._store.recovery_diagnostics(limit=limit)

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
        creation_request_id: str | None = None,
        title: str | None = None,
        skill_selections: tuple[SessionAgentSkillSelection, ...] = (),
    ) -> LocalSession:
        async with self._operation():
            session = await self._sessions.create(
                agent_snapshot=agent_snapshot,
                environment_snapshot=environment_snapshot,
                creation_request_id=creation_request_id,
                title=title,
                skill_selections=skill_selections,
            )
            return await self._complete_session_provisioning(session)

    async def fork_session(
        self,
        source_session_id: str,
        *,
        expected_source_version: int,
        agent_snapshot: SnapshotReference | None = None,
        environment_snapshot: SnapshotReference | None = None,
        creation_request_id: str | None = None,
        title: str | None = None,
        skill_selections: tuple[SessionAgentSkillSelection, ...] | None = None,
    ) -> LocalSession:
        async with self._operation():
            session = await self._sessions.fork(
                source_session_id,
                expected_source_version=expected_source_version,
                agent_snapshot=agent_snapshot,
                environment_snapshot=environment_snapshot,
                creation_request_id=creation_request_id,
                title=title,
                skill_selections=skill_selections,
            )
            return await self._complete_session_provisioning(session)

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
        expected_version: int,
        update: SessionUpdate,
    ) -> LocalSession:
        async with self._operation():
            return await self._sessions.update(
                session_id,
                expected_version=expected_version,
                update=update,
            )

    async def session_environment(self, session_id: str) -> EnvironmentAvailability:
        async with self._operation():
            return await self._environments.availability(session_id)

    async def run_session_turn(
        self,
        session_id: str,
        *,
        thread_id: str,
        expected_thread_version: int,
        input_value: RunInputValue,
    ) -> TurnView:
        async with self._operation():
            return await self._runs.run_turn(
                session_id=session_id,
                thread_id=thread_id,
                expected_thread_version=expected_thread_version,
                input_value=input_value,
            )

    async def resume_session_turn(
        self,
        session_id: str,
        *,
        turn_id: str,
        expected_thread_version: int,
        results: DeferredToolResults,
    ) -> TurnView:
        async with self._operation():
            return await self._runs.resume_turn(
                session_id=session_id,
                turn_id=turn_id,
                expected_thread_version=expected_thread_version,
                results=results,
            )

    async def session_deferred_requests(
        self,
        session_id: str,
        *,
        turn_id: str,
    ) -> DeferredToolRequests:
        async with self._operation():
            return await self._runs.deferred_requests(
                session_id=session_id,
                turn_id=turn_id,
            )

    async def cancel_session_turn(
        self,
        session_id: str,
        *,
        turn_id: str,
        expected_thread_version: int,
    ) -> TurnView:
        async with self._operation():
            return await self._runs.cancel_turn(
                session_id=session_id,
                turn_id=turn_id,
                expected_thread_version=expected_thread_version,
            )

    async def session_events(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
        through_sequence: int | None = None,
        limit: int = 10_000,
    ) -> tuple[PresentationDelivery, ...]:
        async with self._operation():
            return await self._events.replay(
                session_id,
                after_sequence=after_sequence,
                through_sequence=through_sequence,
                limit=limit,
            )

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

    async def retry_session_provisioning(
        self,
        session_id: str,
        *,
        expected_version: int,
    ) -> LocalSession:
        async with self._operation():
            session = await self._sessions.get(session_id)
            if session.control_version != expected_version:
                raise SessionError(
                    "The Session control version is stale.",
                    code="session_version_conflict",
                    details={"current_version": session.control_version},
                )
            if session.lifecycle_state is SessionLifecycleState.blocked:
                await self._sessions.validate_selected_authority(session_id)
                session = await self._sessions.set_lifecycle(
                    session_id,
                    expected_version=expected_version,
                    state=SessionLifecycleState.provisioning,
                )
            return await self._complete_session_provisioning(session)

    async def delete_session(self, session_id: str, *, expected_version: int) -> None:
        async with self._operation():
            current = await self._sessions.get(session_id)
            if current.control_version != expected_version:
                raise SessionError(
                    "The Session control version is stale.",
                    code="session_version_conflict",
                    details={"current_version": current.control_version},
                )
            if current.root.active_turn_id is not None:
                raise SessionError(
                    "A Session with active work cannot be deleted.",
                    code="session_work_active",
                )
            session = await self._sessions.set_lifecycle(
                session_id,
                expected_version=expected_version,
                state=SessionLifecycleState.deleting,
            )
            snapshot = await self._composition.environment(session.environment_snapshot)
            try:
                await self._environments.release_session(session_id, snapshot)
            except BaseException:
                await self._sessions.set_lifecycle(
                    session_id,
                    expected_version=session.control_version,
                    state=SessionLifecycleState.cleanup_pending,
                )
                raise
            await self._sessions.delete(session_id, expected_version=session.control_version)

    async def apply_source_transaction(
        self,
        manifest: SourceTransactionManifest,
        replacements: dict[str, bytes],
    ) -> ConfigurationGeneration:
        async with self._operation():
            return await self._configuration.apply_transaction(manifest, replacements)

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

    async def _complete_session_provisioning(self, session: LocalSession) -> LocalSession:
        if session.lifecycle_state is not SessionLifecycleState.provisioning:
            return session
        snapshot = await self._composition.environment(session.environment_snapshot)
        try:
            if snapshot.definition.lifecycle.provision == "eager":
                await self._environments.provision(session.session_id)
        except Exception as exc:
            return await self._sessions.set_lifecycle(
                session.session_id,
                expected_version=session.control_version,
                state=SessionLifecycleState.blocked,
                failure={"code": getattr(exc, "code", "environment_provisioning_failed")},
            )
        return await self._sessions.set_lifecycle(
            session.session_id,
            expected_version=session.control_version,
            state=SessionLifecycleState.ready,
        )

    async def _stop(self) -> bool:
        async with self._operation_lock:
            if self._state is not HostState.ready:
                return False
            self._state = HostState.stopping
            idle = self._operations_idle

        await self._runs.cancel_all()
        with move_on_after(self._settings.shutdown_timeout_seconds) as drain_scope:
            await idle.wait()
        if not drain_scope.cancel_called:
            return False

        async with self._operation_lock:
            scopes = tuple(self._operation_scopes)
            idle = self._operations_idle
        for scope in scopes:
            scope.cancel()
        # The data-root lease cannot be released while an accepted operation
        # still holds Host authority. Product operation cleanup is required to
        # make cancellation bounded; the final ownership fence waits for it.
        await idle.wait()
        return False

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
    *,
    model_resolver_factory: RunModelResolverFactory = unavailable_run_model_resolver_factory,
) -> AsyncGenerator[AgentUiHost]:
    """Start, expose, and close one complete stable Agent UI Host lifetime."""

    host: AgentUiHost | None = None
    try:
        async with open_local_store(settings.storage) as store:
            host = AgentUiHost(
                settings,
                store,
                model_resolver_factory=model_resolver_factory,
            )
            await host._configuration.initialize()
            await host._envd.recover_staging()
            await host._sessions.initialize()
            await host._environments.initialize()
            await host._events.initialize()
            await store.cleanup_unreferenced_objects(
                retention_seconds=host._configuration.settings.orphan_retention_seconds,
            )
            await host._runtime.start()
            host._state = HostState.ready
            async with create_task_group() as tasks:
                tasks.start_soon(host._configuration.reconcile_periodically)
                tasks.start_soon(host._configuration.reap_skill_leases_periodically)
                try:
                    yield host
                finally:
                    with CancelScope(shield=True):
                        operations_timed_out = await host._stop()
                        tasks.cancel_scope.cancel()
                        cleanup_errors: list[BaseException] = []
                        if operations_timed_out:
                            cleanup_errors.append(
                                HostStateError(
                                    "Accepted Host operations did not stop within the shutdown bound.",
                                    code="host_operation_cleanup_timeout",
                                )
                            )
                        with move_on_after(host._settings.shutdown_timeout_seconds) as runs_scope:
                            try:
                                await host._runs.close()
                            except BaseException as exc:
                                cleanup_errors.append(exc)
                        if runs_scope.cancel_called:
                            cleanup_errors.append(
                                HostStateError(
                                    "Foreground Run cleanup exceeded its shutdown bound.",
                                    code="host_run_cleanup_timeout",
                                )
                            )
                        try:
                            await host._runtime.close()
                        except BaseException as exc:
                            cleanup_errors.append(exc)
                        with move_on_after(host._settings.shutdown_timeout_seconds) as environment_scope:
                            try:
                                await host._environments.close()
                            except BaseException as exc:
                                cleanup_errors.append(exc)
                        if environment_scope.cancel_called:
                            cleanup_errors.append(
                                HostStateError(
                                    "Environment cleanup exceeded its shutdown bound.",
                                    code="host_environment_cleanup_timeout",
                                )
                            )
                        with move_on_after(host._settings.shutdown_timeout_seconds) as composition_scope:
                            try:
                                await host._composition.close()
                            except BaseException as exc:
                                cleanup_errors.append(exc)
                        if composition_scope.cancel_called:
                            cleanup_errors.append(
                                HostStateError(
                                    "Composition cleanup exceeded its shutdown bound.",
                                    code="host_composition_cleanup_timeout",
                                )
                            )
                        with move_on_after(host._settings.shutdown_timeout_seconds) as configuration_scope:
                            try:
                                await host._configuration.close()
                            except BaseException as exc:
                                cleanup_errors.append(exc)
                        if configuration_scope.cancel_called:
                            cleanup_errors.append(
                                HostStateError(
                                    "Configuration cleanup exceeded its shutdown bound.",
                                    code="host_configuration_cleanup_timeout",
                                )
                            )
                        if cleanup_errors:
                            raise BaseExceptionGroup("Agent UI Host cleanup failed.", cleanup_errors)
    finally:
        if host is not None:
            try:
                with CancelScope(shield=True):
                    await host._runtime.close()
            finally:
                host._state = HostState.closed


__all__ = ["AgentUiHost", "HostState", "HostStatus", "open_agent_ui_host"]
