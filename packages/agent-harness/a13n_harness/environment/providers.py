"""Process-local provider and aggregate Environment contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from .commands import (
    BoundProcessHandle,
    CommandRequest,
    PortObservation,
    PortTarget,
    ProcessControlResult,
    ProcessIdentity,
    ProcessInfo,
    ProcessReadOutputResult,
    ProcessSignalResult,
    ProcessStartResult,
    ProcessWriteStdinResult,
    ProviderPortOperations,
    ProviderProcessOperations,
    ProviderShellOperations,
    ShellExecResult,
)
from .files import FileOperator
from .models import (
    EnvironmentAvailability,
    EnvironmentBindingObservation,
    EnvironmentBindingState,
    EnvironmentDescriptor,
    EnvironmentOperationFamily,
    EnvironmentOperationReceipt,
    EnvironmentPath,
    EnvironmentReadinessRequirement,
    EnvironmentState,
    EnvironmentStateLimits,
    EnvironmentTopology,
    EnvironmentTopologyChange,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
)
from .retention import (
    BoundOutputCursor,
    BoundOutputReference,
    EnvironmentOutputPolicy,
    EnvironmentOutputReadResult,
    ProviderOutputOperations,
)

if TYPE_CHECKING:
    from a13n_harness.identity import AgentInstanceContext
    from a13n_harness.model_context import ModelContextProjection, ModelContextProjectionRequest


@dataclass(frozen=True, slots=True)
class EnvironmentProviderOperations:
    """Provider-local semantic operation facets captured at entry."""

    files: FileOperator | None = None
    shell: ProviderShellOperations | None = None
    processes: ProviderProcessOperations | None = None
    ports: ProviderPortOperations | None = None
    outputs: ProviderOutputOperations | None = None


@dataclass(frozen=True, slots=True)
class FileScopeSelection:
    """One exact logical file route captured before a compound operation."""

    logical_path: str
    resolved_path: EnvironmentPath
    observed_generation: str


class FileScopeProvider(Protocol):
    """Select and hold one revision-pinned FileOperator for compound operations."""

    def select_files(self, path: str) -> FileScopeSelection: ...

    def open_files(self, selection: FileScopeSelection) -> AbstractAsyncContextManager[FileOperator]: ...


class BoundShellOperations(Protocol):
    """Alias-aware shell operations routed by one BoundEnvironment."""

    async def exec(self, request: CommandRequest, *, alias: str | None = None) -> ShellExecResult: ...

    async def exec_captured(self, request: CommandRequest, *, alias: str | None = None) -> ShellExecResult:
        """Execute and materialize output under one exact binding-revision lease."""
        ...


class BoundProcessOperations(Protocol):
    """Revision-fenced process operations routed by one BoundEnvironment."""

    async def start(self, request: CommandRequest, *, alias: str | None = None) -> ProcessStartResult: ...

    async def rebind(
        self,
        identity: ProcessIdentity,
        *,
        output_policy: EnvironmentOutputPolicy,
    ) -> ProcessInfo: ...

    async def inspect(self, handle: BoundProcessHandle) -> ProcessInfo: ...

    async def read_output(
        self,
        handle: BoundProcessHandle,
        *,
        stdout_cursor: BoundOutputCursor | None = None,
        stderr_cursor: BoundOutputCursor | None = None,
        stdout_start_offset: int | None = None,
        stderr_start_offset: int | None = None,
        wait_seconds: float = 0,
        policy: EnvironmentOutputPolicy,
    ) -> ProcessReadOutputResult: ...

    async def write_stdin(
        self,
        handle: BoundProcessHandle,
        data: bytes,
        *,
        close_after_write: bool = False,
    ) -> ProcessWriteStdinResult: ...

    async def close_stdin(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt: ...

    async def signal(
        self,
        handle: BoundProcessHandle,
        signal: Literal["interrupt", "terminate"],
    ) -> ProcessSignalResult: ...

    async def wait(
        self,
        handle: BoundProcessHandle,
        *,
        condition: Literal["initial_terminal", "tree_cleaned"],
        timeout_seconds: float,
    ) -> ProcessInfo: ...

    async def kill(self, handle: BoundProcessHandle) -> ProcessControlResult: ...

    async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt: ...


class BoundPortOperations(Protocol):
    """Alias-aware port observations routed by one BoundEnvironment."""

    async def inspect(self, target: PortTarget) -> PortObservation: ...

    async def wait(
        self,
        target: PortTarget,
        *,
        desired: Literal["listening", "not_listening"],
        timeout_seconds: float,
    ) -> PortObservation: ...


class BoundOutputOperations(Protocol):
    """Revision-fenced retained-output operations routed by one BoundEnvironment."""

    async def read(
        self,
        reference: BoundOutputReference,
        *,
        cursor: BoundOutputCursor | None = None,
        start_offset: int | None = None,
        policy: EnvironmentOutputPolicy,
    ) -> EnvironmentOutputReadResult: ...

    async def release(
        self,
        *,
        reference: BoundOutputReference | None = None,
        cursor: BoundOutputCursor | None = None,
    ) -> EnvironmentOperationReceipt: ...


class BoundEnvironmentProvider(Protocol):
    """One entered provider resource with a stable observed generation."""

    @property
    def provider_type(self) -> str: ...

    @property
    def environment_id(self) -> str: ...

    @property
    def descriptor(self) -> EnvironmentDescriptor: ...

    @property
    def availability(self) -> EnvironmentAvailability: ...

    @property
    def operations(self) -> EnvironmentProviderOperations: ...

    async def ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None: ...

    async def export_state(self, *, max_bytes: int) -> EnvironmentBindingState | None: ...

    async def restore_state(self, state: EnvironmentBindingState) -> None: ...


class EnvironmentProviderBinding(ABC):
    """Single-use provider candidate materialized by a trusted Host."""

    def _claim_transfer(self) -> bool:
        """Atomically mark this candidate as transferred without a central identity table."""
        marker_name = "_EnvironmentProviderBinding__transferred"
        try:
            object.__getattribute__(self, marker_name)
        except AttributeError:
            object.__setattr__(self, marker_name, True)
            return True
        return False

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """Return the stable provider compatibility discriminator."""

    @property
    @abstractmethod
    def environment_id(self) -> str:
        """Return the provider's logical resource identity."""

    @abstractmethod
    def bind(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
        binding_id: str,
        binding_revision: int,
    ) -> AbstractAsyncContextManager[BoundEnvironmentProvider]:
        """Enter this candidate exactly once."""

    @abstractmethod
    async def discard(self) -> None:
        """Idempotently dispose a candidate that did not enter successfully."""


class EnvironmentTopologyObserver(Protocol):
    @property
    def initial_topology_version(self) -> int: ...

    async def read(
        self,
        *,
        after_version: int,
        wait: bool = False,
    ) -> tuple[EnvironmentTopologyChange, ...]: ...


class EnvironmentTopologyController(Protocol):
    async def wait_until_active(self) -> None: ...

    async def apply(self, request: EnvironmentTopologyRequest) -> EnvironmentTopologyChange: ...

    def begin_close(self) -> None:
        """Install the logical-run terminal fence without awaiting provider cleanup."""


class BoundEnvironment(ABC):
    """Stable provider-neutral facade available for one logical Harness run."""

    @property
    @abstractmethod
    def topology(self) -> EnvironmentTopology: ...

    @property
    @abstractmethod
    def restored_state_topology_version(self) -> int | None: ...

    @property
    @abstractmethod
    def topology_observer(self) -> EnvironmentTopologyObserver: ...

    @property
    @abstractmethod
    def files(self) -> FileOperator: ...

    @abstractmethod
    def select_files(self, path: str) -> FileScopeSelection:
        """Capture one exact binding revision for a logical file path."""

    @abstractmethod
    def open_files(self, selection: FileScopeSelection) -> AbstractAsyncContextManager[FileOperator]:
        """Hold the selected revision and route all scoped paths through it."""

    @property
    @abstractmethod
    def shell(self) -> BoundShellOperations: ...

    @property
    @abstractmethod
    def processes(self) -> BoundProcessOperations: ...

    @property
    @abstractmethod
    def ports(self) -> BoundPortOperations: ...

    @property
    @abstractmethod
    def outputs(self) -> BoundOutputOperations: ...

    @abstractmethod
    def resolve_path(self, path: str, *, alias: str | None = None) -> EnvironmentPath:
        """Resolve a model-facing selector into one captured internal binding path."""

    @abstractmethod
    async def project_model_context(
        self,
        request: ModelContextProjectionRequest,
    ) -> ModelContextProjection:
        """Project the current provider-neutral topology without editing messages."""

    @abstractmethod
    async def activate(self) -> None:
        """Publish controller activation after initial state restore completes."""

    @abstractmethod
    async def describe(self, binding_id: str) -> EnvironmentBindingObservation: ...

    @abstractmethod
    async def ensure_ready(self, requirement: EnvironmentReadinessRequirement) -> None: ...

    @abstractmethod
    async def export_state(self) -> EnvironmentState: ...

    @abstractmethod
    async def restore_state(self, state: EnvironmentState) -> None: ...


class EnvironmentRunBinding(ABC):
    """Single-use aggregate paired with one Host-retained topology controller."""

    @property
    @abstractmethod
    def controller(self) -> EnvironmentTopologyController: ...

    @property
    @abstractmethod
    def topology_limits(self) -> EnvironmentTopologyLimits: ...

    @property
    @abstractmethod
    def state_limits(self) -> EnvironmentStateLimits: ...

    @abstractmethod
    def bind(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
    ) -> AbstractAsyncContextManager[BoundEnvironment]:
        """Bind and enter the aggregate for exactly one run."""


class RawEnvironmentReader(Protocol):
    def __aiter__(self) -> AsyncIterator[bytes]: ...
