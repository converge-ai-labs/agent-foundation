"""Process-local application boundary shared by Agent UI surfaces."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path

from anyio import CancelScope, Event, Lock, move_on_after
from pydantic import BaseModel, ConfigDict, Field

from a13n_ui.capability_runtime import production_portable_capabilities
from a13n_ui.composition import AgentCompositionResolver, AgentReconstructor, CompositionAcceptanceService
from a13n_ui.configuration import LoadedAgentUiConfiguration
from a13n_ui.environment_runtime import EnvironmentSnapshotReconstructor
from a13n_ui.errors import AppStateError
from a13n_ui.live import AgentUiLiveHub, LiveSubscription
from a13n_ui.session_service import (
    RootCancelResult,
    RootRunOutcome,
    RootSteerResult,
    SessionService,
)
from a13n_ui.settings import AgentUiSettings
from a13n_ui.storage import LocalStore, Session, open_local_store
from a13n_ui.subagent_operator import AgentUiSubagentOperator


class AppState(StrEnum):
    """Observable lifecycle state for one Agent UI App."""

    starting = "starting"
    ready = "ready"
    stopping = "stopping"
    closed = "closed"


class AppStatus(BaseModel):
    """Detached application health projection."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    state: AppState
    instance_id: str = Field(min_length=1, max_length=64)
    object_count: int = Field(ge=0)


class AgentUiApp:
    """The only application boundary shared by Agent UI surfaces."""

    def __init__(
        self,
        settings: AgentUiSettings,
        store: LocalStore,
        *,
        sessions: SessionService,
        subagent_operator: AgentUiSubagentOperator,
        live_hub: AgentUiLiveHub,
    ) -> None:
        self._settings = settings
        self._store = store
        self._sessions = sessions
        self._subagent_operator = subagent_operator
        self._live_hub = live_hub
        self._state = AppState.starting
        self._operation_lock = Lock()
        self._operation_scopes: set[CancelScope] = set()
        self._operations_idle = Event()
        self._operations_idle.set()

    @property
    def state(self) -> AppState:
        return self._state

    async def status(self) -> AppStatus:
        async with self._operation():
            return AppStatus(
                state=self._state,
                instance_id=self._store.app_instance_id,
                object_count=await self._store.object_count(),
            )

    async def create_session(
        self,
        *,
        agent_name: str,
        environment_name: str,
        source_digest: str | None = None,
        title: str | None = None,
    ) -> Session:
        """Create a Session through the process-local application boundary."""

        async with self._operation():
            return await self._sessions.create(
                agent_name=agent_name,
                environment_name=environment_name,
                source_digest=source_digest,
                title=title,
            )

    async def get_session(self, session_id: str) -> Session:
        """Read one detached Session through the application boundary."""

        async with self._operation():
            return await self._sessions.get(session_id)

    async def run_session(
        self,
        *,
        session_id: str,
        prompt: str,
        folders: tuple[Path | str, ...],
    ) -> RootRunOutcome:
        """Execute one admitted root message through the shared Session service."""

        async with self._operation():
            return await self._sessions.run(
                session_id=session_id,
                prompt=prompt,
                folders=folders,
            )

    async def steer_session(self, *, session_id: str, message: str) -> RootSteerResult:
        """Steer one root Run currently owned by this App process."""

        async with self._operation():
            return await self._sessions.steer(session_id=session_id, message=message)

    async def cancel_session(self, *, session_id: str) -> RootCancelResult:
        """Request cancellation of one root Run currently owned by this App process."""

        async with self._operation():
            return await self._sessions.cancel(session_id=session_id)

    @asynccontextmanager
    async def live_events(
        self,
        *,
        session_id: str | None = None,
        thread_id: str | None = None,
    ) -> AsyncGenerator[LiveSubscription]:
        """Replay and follow bounded process-local events for one surface."""

        self._require_ready()
        async with self._live_hub.subscribe(session_id=session_id, thread_id=thread_id) as subscription:
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
            raise AppStateError(
                "Agent UI operation was cancelled during App shutdown.",
                code="app_stopping",
            )

    async def _stop(self) -> None:
        async with self._operation_lock:
            if self._state is not AppState.ready:
                return
            self._state = AppState.stopping
            idle = self._operations_idle

        with move_on_after(self._settings.shutdown_timeout_seconds) as drain_scope:
            await idle.wait()
        if not drain_scope.cancel_called:
            return

        async with self._operation_lock:
            scopes = tuple(self._operation_scopes)
            idle = self._operations_idle
        for scope in scopes:
            scope.cancel()
        with move_on_after(self._settings.shutdown_timeout_seconds):
            await idle.wait()

    async def _close_collaborators(self) -> None:
        try:
            await self._subagent_operator.close(timeout_seconds=self._settings.shutdown_timeout_seconds)
        finally:
            await self._live_hub.close()

    def _require_ready(self) -> None:
        if self._state is not AppState.ready:
            raise AppStateError(
                "Agent UI App is not accepting commands.",
                code="app_not_ready",
                details={"state": self._state.value},
            )


@asynccontextmanager
async def open_agent_ui_app(
    settings: AgentUiSettings,
    *,
    configuration: LoadedAgentUiConfiguration | None = None,
) -> AsyncGenerator[AgentUiApp]:
    """Start, expose, and close one complete process-local App lifetime."""

    app: AgentUiApp | None = None
    operator: AgentUiSubagentOperator | None = None
    try:
        async with open_local_store(settings.storage) as store:
            if configuration is not None:
                current_digest = await store.configurations.current_digest()
                await CompositionAcceptanceService(store, AgentCompositionResolver()).accept(
                    configuration,
                    expected_current_digest=current_digest,
                )
            environment_reconstructor = EnvironmentSnapshotReconstructor(
                envd_settings=settings.envd_runtime,
                local_runtime_parent=store.layout.runtimes,
            )
            live_hub = AgentUiLiveHub()
            cleanup_timeout = min(
                settings.shutdown_timeout_seconds,
                settings.storage.cleanup_timeout_seconds,
            )
            operator = AgentUiSubagentOperator(
                store=store,
                environment_reconstructor=environment_reconstructor,
                live_hub=live_hub,
                cleanup_timeout_seconds=cleanup_timeout,
            )
            sessions = SessionService(
                store=store,
                agent_reconstructor=AgentReconstructor(
                    portable_capabilities=production_portable_capabilities(),
                ),
                environment_reconstructor=environment_reconstructor,
                subagent_operator=operator,
                live_hub=live_hub,
                cleanup_timeout_seconds=cleanup_timeout,
            )
            app = AgentUiApp(
                settings,
                store,
                sessions=sessions,
                subagent_operator=operator,
                live_hub=live_hub,
            )
            try:
                await operator.start()
                app._state = AppState.ready
                yield app
            finally:
                with CancelScope(shield=True):
                    await app._stop()
                await app._close_collaborators()
                app._state = AppState.closed
    finally:
        if app is not None:
            app._state = AppState.closed
        elif operator is not None:
            await operator.close(timeout_seconds=settings.shutdown_timeout_seconds)


__all__ = ["AgentUiApp", "AppState", "AppStatus", "open_agent_ui_app"]
