"""Surface-neutral Agent UI application composition root."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from enum import StrEnum

from anyio import CancelScope, Event, Lock, move_on_after
from pydantic import BaseModel, ConfigDict, Field

from converge_agent_ui.errors import ApplicationStateError
from converge_agent_ui.settings import AgentUiSettings
from converge_agent_ui.storage import LocalStore, StoreDiagnostic, open_local_store


class ApplicationState(StrEnum):
    """Observable lifecycle state for one process-local application instance."""

    starting = "starting"
    ready = "ready"
    stopping = "stopping"
    closed = "closed"


class ApplicationStatus(BaseModel):
    """Detached safe application health projection."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    state: ApplicationState
    process_generation: str = Field(min_length=1, max_length=64)
    registered_object_count: int = Field(ge=0)


class AgentUiApplication:
    """The only product boundary shared by Agent UI presentation surfaces."""

    def __init__(self, settings: AgentUiSettings, store: LocalStore) -> None:
        self._settings = settings
        self._store = store
        self._state = ApplicationState.starting
        self._operation_lock = Lock()
        self._operation_scopes: set[CancelScope] = set()
        self._operations_idle = Event()
        self._operations_idle.set()

    @property
    def state(self) -> ApplicationState:
        return self._state

    async def status(self) -> ApplicationStatus:
        async with self._operation():
            return ApplicationStatus(
                state=self._state,
                process_generation=self._store.process_generation,
                registered_object_count=await self._store.object_count(),
            )

    async def recovery_diagnostics(self, *, limit: int = 100) -> tuple[StoreDiagnostic, ...]:
        async with self._operation():
            return await self._store.recovery_diagnostics(limit=limit)

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
            raise ApplicationStateError(
                "Agent UI operation was cancelled during application shutdown.",
                code="application_stopping",
            )

    async def _stop(self) -> None:
        async with self._operation_lock:
            if self._state is not ApplicationState.ready:
                return
            self._state = ApplicationState.stopping
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
        await idle.wait()

    def _require_ready(self) -> None:
        if self._state is not ApplicationState.ready:
            raise ApplicationStateError(
                "Agent UI application is not accepting commands.",
                code="application_not_ready",
                details={"state": self._state.value},
            )


@asynccontextmanager
async def open_application(settings: AgentUiSettings) -> AsyncGenerator[AgentUiApplication]:
    """Start, expose, and close one complete Agent UI application lifetime."""

    application: AgentUiApplication | None = None
    try:
        async with open_local_store(settings.storage) as store:
            application = AgentUiApplication(settings, store)
            application._state = ApplicationState.ready
            try:
                yield application
            finally:
                with CancelScope(shield=True):
                    await application._stop()
    finally:
        if application is not None:
            application._state = ApplicationState.closed


__all__ = ["AgentUiApplication", "ApplicationState", "ApplicationStatus", "open_application"]
