"""Online admission evidence refreshed only after relational locks are released."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.environments.devices import DeviceDiscovery, DeviceTarget
from a13n_service.environments.errors import EnvironmentManagementError, connection_dependency_unavailable
from a13n_service.storage import transaction

from .coordination import ConfirmedObservation, ConnectionCoordination, CoordinationError


class _EvidenceRequired(Exception):
    def __init__(self, organization_id: str, environment_id: str) -> None:
        self.target = (organization_id, environment_id)
        super().__init__("Authorized Environment requires fresh online evidence")


class _DeviceRequired(Exception):
    def __init__(self, target: DeviceTarget) -> None:
        self.target = target
        super().__init__("Authorized binding requires an explicit Device working directory")


class OnlineEvidence:
    """One acceptance's observations; resource authorization belongs to its transaction."""

    def __init__(
        self,
        observations: dict[tuple[str, str], ConfirmedObservation],
        directories: dict[tuple[str, str, str], str] | None = None,
    ) -> None:
        self._observations = observations
        self._directories = directories if directories is not None else {}
        self._required: set[tuple[str, str]] = set()

    def working_directory(self, target: DeviceTarget) -> str:
        directory = self._directories.get(target.key)
        if directory is None:
            raise _DeviceRequired(target)
        return directory

    def require(self, organization_id: str, environment_id: str) -> None:
        target = (organization_id, environment_id)
        self._required.add(target)
        observation = self._observations.get(target)
        if observation is None or observation.deadline().monotonic_at <= monotonic():
            raise _EvidenceRequired(*target)

    def validate(self) -> None:
        """Reject evidence that expired while the acceptance transaction was running."""
        for target in self._required:
            self.require(*target)


class OnlineAdmission:
    """Retry the whole DB acceptance with fresh evidence, never suspend locks for Redis.

    Callbacks contain relational work only and must tolerate transaction rollback.
    Each retry reauthorizes and resolves the selection under the ordinary locks.
    No coordination dependency is needed for non-WebSocket selections.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        coordination: ConnectionCoordination | None = None,
        *,
        devices: DeviceDiscovery | None = None,
    ) -> None:
        self._sessions = sessions
        self._coordination = coordination
        self._devices = devices

    async def commit[T](self, accept: Callable[[AsyncSession, OnlineEvidence], Awaitable[T]]) -> T:
        # Ordinary selections need no connection budget. Once online evidence
        # is required, both SQL retries and coordination share one deadline.
        budget = asyncio.timeout(None)
        try:
            async with budget:
                observations: dict[tuple[str, str], ConfirmedObservation] = {}
                directories: dict[tuple[str, str, str], str] = {}
                for attempt in range(4):
                    evidence = OnlineEvidence(observations, directories)
                    try:
                        async with transaction(self._sessions) as database:
                            result = await accept(database, evidence)
                            await database.flush()
                            evidence.validate()
                        return result
                    except _DeviceRequired as required:
                        if self._devices is None or attempt == 3:
                            raise connection_dependency_unavailable() from required
                        if budget.when() is None:
                            budget.reschedule(monotonic() + 5)
                        descriptor = await self._devices.describe(required.target)
                        directories[required.target.key] = descriptor.default_working_directory
                    except _EvidenceRequired as required:
                        if self._coordination is None or attempt == 3:
                            raise connection_dependency_unavailable() from required
                        if budget.when() is None:
                            budget.reschedule(monotonic() + 5)
                        try:
                            observation = await self._coordination.observe(*required.target)
                        except CoordinationError as error:
                            raise connection_dependency_unavailable() from error
                        value = observation.value
                        if value.status != "online" or value.connection is None or value.retiring is not None:
                            raise EnvironmentManagementError(
                                "environment_unavailable",
                                "Client Environment requires an initialized online connection.",
                                category=ErrorCategory.conflict,
                            ) from None
                        observations[required.target] = observation
                raise connection_dependency_unavailable()
        except TimeoutError as error:
            if not budget.expired():
                raise
            raise connection_dependency_unavailable() from error
