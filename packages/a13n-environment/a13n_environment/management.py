"""Explicit management of backend targets; execution ownership is separate."""

from __future__ import annotations

import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .execution import EnvironmentConnector
from .models import EnvironmentState


@runtime_checkable
class ClosableRuntime(Protocol):
    """A client or Device with an explicit owner that outlives its borrowers."""

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class EnvironmentStatus:
    status: Literal["running", "stopped", "absent"]
    state: EnvironmentState | None


class EnvironmentProvider[E: BaseModel](ABC):
    """Account-scoped management authority, used only under Host lifecycle policy.

    The recipe accompanies saved state because existing state codecs deliberately
    exclude execution options and the complete provisioning configuration.
    """

    @abstractmethod
    async def create(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None = None
    ) -> EnvironmentState | None: ...

    @abstractmethod
    async def start(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> EnvironmentState | None: ...

    @abstractmethod
    async def inspect(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> EnvironmentStatus: ...

    @abstractmethod
    async def stop(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> EnvironmentState | None: ...

    @abstractmethod
    async def destroy(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> None: ...

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
    def execution_connector(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> EnvironmentConnector: ...

    @abstractmethod
    async def close(self) -> None: ...

    async def __aenter__(self) -> EnvironmentProvider[E]:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


class EnvironmentProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HostLocalProviderConfiguration(EnvironmentProviderConfiguration):
    host_id: str = Field(default_factory=socket.gethostname, min_length=1, max_length=256)
