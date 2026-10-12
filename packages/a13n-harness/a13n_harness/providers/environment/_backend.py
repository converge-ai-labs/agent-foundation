"""Separate internal contracts for target management and execution."""

from __future__ import annotations

from abc import ABC, abstractmethod

from a13n_harness.environment.models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from a13n_harness.environment.operations import EnvironmentOperations


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
