"""Separate internal contracts for target management and execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Literal

from .errors import EnvironmentProviderErrorCategory, provider_error
from .models import EnvironmentAvailability, EnvironmentDescriptor, EnvironmentOperationFamily, EnvironmentState
from .operations import EnvironmentOperations


class BackendReference(ABC):
    def __init__(self, state: EnvironmentState | None) -> None:
        self._known_state = state.model_copy(deep=True) if state is not None else None

    @property
    @abstractmethod
    def provider_key(self) -> str: ...

    @property
    @abstractmethod
    def environment_id(self) -> str: ...

    @property
    def state(self) -> EnvironmentState | None:
        return self._known_state.model_copy(deep=True) if self._known_state is not None else None

    @abstractmethod
    async def close(self) -> None: ...


class ManagementBackend(BackendReference):
    """Lifecycle authority and mutable observations, without execution operations."""

    def _cache_state(self, state: EnvironmentState | None) -> None:
        self._known_state = state.model_copy(deep=True) if state is not None else None

    def _unsupported(self) -> Exception:
        return provider_error(
            self.provider_key, "provider_operation_unsupported", EnvironmentProviderErrorCategory.UNSUPPORTED
        )

    def validate_management(self) -> None:
        """Validate saved ownership before any management dispatch."""
        return None

    async def create(self) -> None:
        raise self._unsupported()

    async def start(self) -> None:
        raise self._unsupported()

    async def inspect(self) -> Literal["running", "stopped", "absent"]:
        raise self._unsupported()

    async def stop(self) -> None:
        raise self._unsupported()

    async def destroy(self) -> None:
        raise self._unsupported()

    @property
    def keepalive_horizon(self) -> timedelta:
        return timedelta(seconds=300)

    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime | None:
        raise self._unsupported()

    async def close(self) -> None:
        return None


class ExecutionBackend(BackendReference):
    """One execution scope on an already selected target, without lifecycle authority."""

    @property
    @abstractmethod
    def descriptor(self) -> EnvironmentDescriptor: ...

    @property
    @abstractmethod
    def availability(self) -> EnvironmentAvailability: ...

    @property
    @abstractmethod
    def operations(self) -> EnvironmentOperations: ...

    @abstractmethod
    async def open(self, *, execution_id: str) -> None: ...

    @abstractmethod
    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
