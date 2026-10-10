"""Process-local provider and aggregate Environment contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from a13n_environment.commands import (
    BoundProcessHandle,
    CommandRequest,
    PortObservation,
    PortTarget,
    ProcessControlResult,
    ProcessDiscovery,
    ProcessIdentity,
    ProcessInfo,
    ProcessReadOutputResult,
    ProcessSignalResult,
    ProcessStartResult,
    ProcessWriteStdinResult,
    ShellExecResult,
)
from a13n_environment.computer import (
    ComputerActionResult,
    ComputerDescription,
    ComputerInput,
    ComputerScreenshot,
)
from a13n_environment.execution import EnvironmentExecution
from a13n_environment.files import FileOperator
from a13n_environment.models import (
    EnvironmentAction,
    EnvironmentOperationReceipt,
    EnvironmentState,
)
from a13n_environment.retention import (
    BoundOutputCursor,
    BoundOutputReference,
    EnvironmentOutputPolicy,
    EnvironmentOutputReadResult,
)

from a13n_harness.environment.models import (
    EnvironmentChange,
    EnvironmentMountObservation,
    EnvironmentPath,
    EnvironmentReadinessRequirement,
    EnvironmentSnapshot,
)

if TYPE_CHECKING:
    from a13n_harness.identity import AgentInstanceContext
    from a13n_harness.model_context import ModelContextProjection, ModelContextProjectionRequest

    from .sources import EnvironmentEntry


@dataclass(frozen=True, slots=True)
class FileScopeSelection:
    """One exact aggregate file route captured before a compound operation."""

    logical_path: str
    resolved_path: EnvironmentPath
    observed_generation: str
    mount_path: str | None = None


class FileScopeProvider(Protocol):
    """Select and hold one mount-incarnation-pinned FileOperator for compound operations."""

    async def resolve_files(self, path: str) -> FileScopeSelection: ...

    def select_files(self, path: str) -> FileScopeSelection: ...

    def open_files(self, selection: FileScopeSelection) -> AbstractAsyncContextManager[FileOperator]: ...


class BoundComputerOperations(Protocol):
    async def describe(self, *, alias: str | None = None) -> ComputerDescription: ...

    async def observe(
        self, *, alias: str | None = None, target_id: str | None = None, max_dimension: int = 1280
    ) -> ComputerScreenshot: ...

    async def execute(self, request: ComputerInput, *, alias: str | None = None) -> ComputerActionResult: ...


class BoundShellOperations(Protocol):
    """Alias-aware shell operations routed by one BoundEnvironment."""

    async def exec(self, request: CommandRequest, *, alias: str | None = None) -> ShellExecResult: ...

    async def exec_captured(
        self,
        request: CommandRequest,
        *,
        alias: str | None = None,
        expected_mount_id: str | None = None,
    ) -> ShellExecResult:
        """Dispatch provider-bounded foreground execution under one exact mount-incarnation lease."""
        ...


class BoundProcessOperations(Protocol):
    async def list(self, *, alias: str | None = None, limit: int = 50) -> ProcessDiscovery: ...

    """Revision-fenced process operations routed by one BoundEnvironment."""

    async def start(
        self,
        request: CommandRequest,
        *,
        alias: str | None = None,
        required_actions: frozenset[EnvironmentAction] = frozenset({EnvironmentAction.PROCESS_START}),
        expected_mount_id: str | None = None,
    ) -> ProcessStartResult: ...

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

    async def inspect(self, target: PortTarget, *, alias: str | None = None) -> PortObservation: ...

    async def wait(
        self,
        target: PortTarget,
        *,
        alias: str | None = None,
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


def _claim_execution(execution: EnvironmentExecution, owner: object) -> bool:
    """Keep ownership on the execution across mount wrappers and independent Runs."""
    marker = "_a13n_harness_execution_owner"
    try:
        previous = object.__getattribute__(execution, marker)
    except AttributeError:
        object.__setattr__(execution, marker, owner)
        return True
    return previous is owner


class BoundEnvironment(ABC):
    """Stable provider-neutral facade available for one logical Harness run."""

    @property
    @abstractmethod
    def snapshot(self) -> EnvironmentSnapshot: ...

    @property
    @abstractmethod
    def files(self) -> FileOperator: ...

    @abstractmethod
    def select_files(self, path: str, *, alias: str | None = None) -> FileScopeSelection:
        """Capture one exact mount incarnation for a logical file path."""

    async def resolve_files(self, path: str, *, alias: str | None = None) -> FileScopeSelection:
        """Prepare the selected files mount and return current resource metadata."""
        selected = self.select_files(path, alias=alias)
        async with self.open_files(selected):
            return self.select_files(path, alias=alias)

    @abstractmethod
    def open_files(self, selection: FileScopeSelection) -> AbstractAsyncContextManager[FileOperator]:
        """Hold the selected mount incarnation and route all scoped paths through it."""

    @property
    @abstractmethod
    def shell(self) -> BoundShellOperations: ...

    @property
    @abstractmethod
    def computer(self) -> BoundComputerOperations: ...

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
    def _select_command_actions(
        self,
        request: CommandRequest,
        *,
        alias: str | None,
        actions: frozenset[EnvironmentAction],
    ) -> tuple[str, bool]:
        """Select one exact command mount and report its effective actions."""

    @abstractmethod
    def resolve_path(self, path: str, *, alias: str | None = None) -> EnvironmentPath:
        """Resolve a model-facing selector into one captured internal mount path."""

    @abstractmethod
    async def project_model_context(
        self,
        request: ModelContextProjectionRequest,
    ) -> ModelContextProjection:
        """Project the current provider-neutral mount set without editing messages."""

    @abstractmethod
    async def describe(self, name: str) -> EnvironmentMountObservation: ...

    @abstractmethod
    async def ensure_ready(self, requirement: EnvironmentReadinessRequirement) -> None: ...

    @abstractmethod
    def dump_states(self) -> Mapping[str, EnvironmentState]: ...

    @property
    @abstractmethod
    def _change_sequence(self) -> int:
        """Return the current run-local change sequence for Harness event fencing."""

    @abstractmethod
    async def _read_changes(
        self,
        *,
        after_sequence: int,
        wait: bool = False,
    ) -> tuple[EnvironmentChange, ...]:
        """Read run-local mount changes for Harness event adaptation."""


class EnvironmentRuntime(ABC):
    """Single-use Host authority for one run's Environment mount set."""

    @abstractmethod
    def bind(
        self,
        *,
        thread_id: str,
        run_id: str,
        instance: AgentInstanceContext,
        host_refs: Mapping[str, str],
    ) -> AbstractAsyncContextManager[BoundEnvironment]:
        """Bind and enter the aggregate for exactly one run."""

    @abstractmethod
    async def wait_until_active(self) -> None:
        """Wait until initial entry, state restore, and run-extension activation complete."""

    @abstractmethod
    async def mount(
        self,
        name: str,
        mount: EnvironmentEntry,
        *,
        make_default: bool = False,
    ) -> EnvironmentChange:
        """Register and atomically publish one new mount without activation."""

    @abstractmethod
    async def replace(self, name: str, mount: EnvironmentEntry) -> EnvironmentChange:
        """Validate and atomically replace one existing mount without activation."""

    @abstractmethod
    async def unmount(self, name: str) -> EnvironmentChange:
        """Atomically remove one mount and retire its provider scope."""

    @abstractmethod
    async def set_default(self, name: str | None) -> EnvironmentChange:
        """Atomically select or clear the default mount."""

    @abstractmethod
    async def _activate(self) -> None:
        """Activate the entered aggregate after optional state restoration."""

    @abstractmethod
    def _begin_close(self) -> None:
        """Install the terminal mutation fence before result delivery."""
