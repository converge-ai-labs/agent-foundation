"""Fixed-target connectors and ready execution scopes, independent of any Host."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType

from a13n_harness.environment.commands import BoundProcessHandle
from a13n_harness.environment.models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from a13n_harness.environment.operations import EnvironmentOperations


class EnvironmentConnector(ABC):
    """Inert connection inputs for one fixed target; each open owns a fresh scope."""

    @property
    @abstractmethod
    def provider_key(self) -> str: ...

    @property
    @abstractmethod
    def environment_id(self) -> str: ...

    @property
    @abstractmethod
    def state(self) -> EnvironmentState | None: ...

    @property
    @abstractmethod
    def descriptor(self) -> EnvironmentDescriptor:
        """Configured capabilities, available before any connection I/O."""
        ...

    @abstractmethod
    async def open(self) -> EnvironmentExecution:
        """Open a ready scope without creating, starting, replacing or renewing its target."""
        ...


class EnvironmentExecution(ABC):
    """An open scope owning execution clients, Sessions and observations."""

    @property
    @abstractmethod
    def provider_key(self) -> str: ...

    @property
    @abstractmethod
    def environment_id(self) -> str: ...

    @property
    @abstractmethod
    def execution_id(self) -> str: ...

    @property
    @abstractmethod
    def state(self) -> EnvironmentState | None: ...

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
    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        """Observe this open scope without reconnecting or changing target lifecycle."""
        ...

    async def cleanup_process(self, handle: BoundProcessHandle) -> None:
        """Release this scope's process observation without readiness or reacquisition.

        Implementations retain their own process lifetime policy. This does not
        certify process completion, and cleanup failures must still propagate.
        """
        processes = self.operations.processes
        if processes is None:
            raise EnvironmentError("Process observations are unsupported", code="environment_unsupported")
        await processes.release(handle)

    @abstractmethod
    async def close(self) -> None:
        """Release this scope's resources; never stop or destroy its target."""
        ...

    async def __aenter__(self) -> EnvironmentExecution:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            await self.close()
        except BaseException as cleanup_error:
            if exc_value is None:
                raise
            exc_value.add_note(f"Environment execution cleanup also failed: {cleanup_error!r}")
