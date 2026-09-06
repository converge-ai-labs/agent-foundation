"""Bounded lock discovery for a Supervisor that never claims or executes Runs."""

from __future__ import annotations

from datetime import timedelta

from a13n_logging import get_logger
from anyio import Event, move_on_after
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.interactions.scheduling import AttemptScheduler, ScanPosition
from a13n_service.plugins.commands import PluginRuntimeCommandFailure
from a13n_service.plugins.models import PluginRuntimeStateRecord
from a13n_service.plugins.runner_protocol import PluginRunnerProtocolError
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
from a13n_service.plugins.runtime import PluginRuntimeLockError, PluginRuntimeLockStore
from a13n_service.settings import Settings
from a13n_service.storage import short_session

logger = get_logger(__name__)


class RunnerDiscoveryLoop:
    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        locks: PluginRuntimeLockStore,
        supervisor: PluginRunnerSupervisor,
        *,
        lifecycle: LifecycleWriter,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._locks = locks
        self._supervisor = supervisor
        self._scheduler = AttemptScheduler(sessions, lifecycle=lifecycle)
        self._started = Event()
        self._draining = Event()
        self._stopped = Event()
        self._available = False
        self._position: ScanPosition | None = None

    @property
    def ready(self) -> bool:
        return self._available and self._supervisor.ready and not self.is_draining()

    def is_draining(self) -> bool:
        return self._draining.is_set()

    async def wait_started(self) -> None:
        await self._started.wait()

    async def wait_stopped(self) -> None:
        await self._stopped.wait()

    async def drain(self) -> None:
        self._draining.set()
        await self._supervisor.drain()

    async def run(self) -> None:
        try:
            while not self.is_draining():
                try:
                    await self._restore_active()
                    await self._supervisor.restore_exited()
                    self._available = True
                except (
                    PluginRuntimeCommandFailure,
                    PluginRunnerProtocolError,
                    PluginRuntimeLockError,
                    ValidationError,
                ):
                    if self._available:
                        logger.warning("runner_execution_unavailable")
                    self._available = False
                self._started.set()
                if self._available and not self.is_draining():
                    await self._discover()
                with move_on_after(self._settings.worker_poll_interval_seconds):
                    await self._draining.wait()
        finally:
            self._available = False
            self._stopped.set()

    async def _restore_active(self) -> None:
        async with short_session(self._sessions) as database:
            state = await database.get(PluginRuntimeStateRecord, "runtime")
            if state is None:
                raise PluginRuntimeLockError("plugin_runtime_unavailable")
            if state.mode != "runner":
                raise RuntimeError("configured Plugin Runtime mode conflicts with persisted Plugin Runtime state")
            digest = state.active_lock_digest
            lock = await self._locks.require(database, digest, mode="runner") if digest is not None else None
        if lock is not None and not self.is_draining():
            await self._supervisor.ensure_execution(lock, catalog_active=True)

    async def _discover(self) -> None:
        candidates = await self._scheduler.discover(
            worker_build_id=self._supervisor.build_id,
            handoff_preference_window=timedelta(
                seconds=self._settings.worker_handoff_preference_seconds or self._settings.worker_lease_seconds
            ),
            limit=self._settings.worker_scan_limit,
            after=self._position,
        )
        seen: set[str] = set()
        for candidate in candidates:
            if self.is_draining():
                break
            self._position = candidate.position
            if candidate.runtime_lock_digest in seen:
                continue
            seen.add(candidate.runtime_lock_digest)
            try:
                async with short_session(self._sessions) as database:
                    lock = await self._locks.require(database, candidate.runtime_lock_digest, mode="runner")
                if not self.is_draining():
                    await self._supervisor.ensure_execution(lock)
            except (PluginRuntimeCommandFailure, PluginRunnerProtocolError, PluginRuntimeLockError, ValidationError):
                # Incompatible historical work stays eligible; it never gets another lock.
                logger.debug(
                    "runner_lock_discovery_declined", extra={"runtime_lock_digest": candidate.runtime_lock_digest}
                )
        if len(candidates) < self._settings.worker_scan_limit:
            self._position = None
