"""Explicit management of backend targets; execution ownership is separate."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, runtime_checkable

from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.remote_envd.managed import ManagedCredentialCache
from pydantic import BaseModel

from .desktop_access import Reply, Request
from .metrics import DeviceObservation, MetricsLaunch, ScrapeTarget
from .websocket import WebSocketBootstrap


@dataclass(frozen=True, slots=True)
class EnvironmentStatus:
    status: Literal["running", "stopped", "absent"]
    state: EnvironmentState | None


@dataclass(frozen=True, slots=True)
class Pending:
    """A bounded step finished; continue the same operation after the requested delay.

    `settled` permits replacing this operation with cleanup only when no outstanding
    request can allocate additional resources and state identifies everything to clean.
    Native preparation (for example scheduling an existing Pod) may still be underway.
    """

    state: EnvironmentState | None
    retry_after_seconds: float = 2
    settled: bool = False

    def __post_init__(self) -> None:
        if not 0 < self.retry_after_seconds <= 300:
            raise ValueError("retry_after_seconds must be between 0 and 300")


@dataclass(frozen=True, slots=True)
class Completed:
    """The requested lifecycle transition and its required external effects completed."""

    state: EnvironmentState | None = None


type ManagementResult = Pending | Completed | EnvironmentState | None


class DesktopAccessSession(Protocol):
    """Fixed-target desktop access borrowed for one management transition."""

    async def desktop_access(self, request: Request) -> Reply: ...


@runtime_checkable
class DesktopSessionProvider(Protocol):
    """Optional scoped access; providers without it keep their operation lifetimes."""

    def desktop_session(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None, operation_id: str
    ) -> AbstractAsyncContextManager[DesktopAccessSession]: ...


class EnvironmentProvider[E: BaseModel](ABC):
    """Account-scoped management authority, used under Host lifecycle and access policy.

    The recipe accompanies saved state because existing state codecs deliberately
    exclude execution options and the complete provisioning configuration.
    """

    def configure_envd_version(self, version: str) -> None:
        raise NotImplementedError("Managed Envd upgrades are unavailable for this provider")

    def configure_websocket(self, bootstrap: WebSocketBootstrap) -> None:
        raise NotImplementedError("Outbound WebSocket bootstrap is unavailable for this provider")

    @abstractmethod
    async def create(
        self,
        environment: object,
        *,
        environment_id: str,
        operation_id: str,
        state: EnvironmentState | None = None,
        metrics: MetricsLaunch | None = None,
    ) -> ManagementResult: ...

    @abstractmethod
    async def start(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> ManagementResult: ...

    @abstractmethod
    async def inspect(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> EnvironmentStatus: ...

    @abstractmethod
    async def stop(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> ManagementResult: ...

    @abstractmethod
    async def destroy(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> ManagementResult: ...

    @abstractmethod
    async def keepalive(
        self,
        environment: object,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        deadline: datetime,
        operation_id: str,
    ) -> datetime | None: ...

    @abstractmethod
    def keepalive_horizon(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> timedelta: ...

    @abstractmethod
    async def close(self) -> None: ...

    async def scrape_target(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> ScrapeTarget:
        """Resolve protected collector inputs without a Session or target lifecycle operation."""
        raise NotImplementedError("HTTP metric scraping is unavailable for this provider")

    async def observe_device(
        self,
        environment: object,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        credential_cache: ManagedCredentialCache | None = None,
    ) -> DeviceObservation:
        """Read cached Device observations; never create a Session or change target lifecycle."""
        raise NotImplementedError("Device observation is unavailable for this provider")

    async def desktop_access(
        self,
        environment: object,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        operation_id: str,
        request: Request,
    ) -> Reply:
        """Optional management authority, unavailable until the provider implements enforcement."""
        raise NotImplementedError("Desktop access is unavailable for this provider")

    async def __aenter__(self) -> EnvironmentProvider[E]:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
