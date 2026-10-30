"""Provider-neutral run-scoped Environment API."""

from typing import TYPE_CHECKING, Any

from a13n_harness.environment.archives import FileArchive
from a13n_harness.environment.commands import (
    ArgvCommand,
    BoundProcessHandle,
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    PortObservation,
    PortTarget,
    ProcessControlResult,
    ProcessDiscovery,
    ProcessIdentity,
    ProcessInfo,
    ProcessOutputSnapshot,
    ProcessReadOutputResult,
    ProcessSignalResult,
    ProcessStartResult,
    ProcessStatus,
    ProcessStreamRead,
    ProcessWriteStdinResult,
    ShellCommand,
    ShellExecResult,
)
from a13n_harness.environment.execution import EnvironmentConnector, EnvironmentExecution
from a13n_harness.environment.files import (
    FileBatchValue,
    FileCopyResult,
    FileEntriesResult,
    FileIgnoreMode,
    FileMetadata,
    FileMkdirOperation,
    FileMutationResult,
    FileOperation,
    FileOperator,
    FilePatchResult,
    FileQueryRequest,
    FileRemoveOperation,
    FileStatOperation,
    FileTextMatch,
    FileTextResult,
    FileTextSearchRequest,
    FileTextSearchResult,
    FileWriteMode,
    FileWriteResult,
    FileWriteTextOperation,
)
from a13n_harness.environment.models import (
    DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS,
    ENVIRONMENT_ACTION_CATALOG_VERSION,
    ENVIRONMENT_ACTION_DISPATCH,
    FILE_ACTIONS,
    FILE_READ_ACTIONS,
    EnvironmentAction,
    EnvironmentActionDispatch,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentMountDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from a13n_harness.environment.mounts import (
    EnvironmentChange,
    EnvironmentMountInfo,
    EnvironmentMountObservation,
    EnvironmentPath,
    EnvironmentReadinessRequirement,
    EnvironmentSnapshot,
)
from a13n_harness.environment.retention import (
    BoundOutputCursor,
    BoundOutputReference,
    EnvironmentOutputCapture,
    EnvironmentOutputPolicy,
    EnvironmentOutputReadResult,
    EnvironmentOutputSegment,
    OpaqueOutputCursor,
    OpaqueOutputReference,
    OpaqueProcessHandle,
)

from .extension_factories import (
    ENVIRONMENT_RUN_EXTENSION_ENTRY_POINT_GROUP,
    EnvironmentRunExtensionFactory,
    EnvironmentRunExtensionFactoryCatalog,
    EnvironmentRunExtensionFactoryContext,
    EnvironmentRunExtensionFactoryReference,
    EnvironmentRunExtensionFactoryRegistration,
    build_environment_run_extension_factory_catalog,
    discover_environment_run_extension_factory_references,
)
from .extensions import (
    EnvironmentRunCallback,
    EnvironmentRunCallbacks,
    EnvironmentRunExtension,
    EnvironmentRunExtensionContext,
)
from .providers import FileScopeSelection
from .sources import EnvironmentEntry, EnvironmentMount, EnvironmentSource
from .virtual_files import VirtualFileOperator

if TYPE_CHECKING:
    from .configuration import DynamicEnvironmentConfiguration
    from .dynamic import DynamicEnvironmentCapability


def __getattr__(name: str) -> Any:
    if name in {
        "DynamicEnvironmentCapability",
        "DynamicEnvironmentConfiguration",
    }:
        from .configuration import DynamicEnvironmentConfiguration
        from .dynamic import DynamicEnvironmentCapability

        return {
            "DynamicEnvironmentCapability": DynamicEnvironmentCapability,
            "DynamicEnvironmentConfiguration": DynamicEnvironmentConfiguration,
        }[name]
    raise AttributeError(name)


__all__ = [
    "DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS",
    "ENVIRONMENT_ACTION_CATALOG_VERSION",
    "ENVIRONMENT_ACTION_DISPATCH",
    "ENVIRONMENT_RUN_EXTENSION_ENTRY_POINT_GROUP",
    "FILE_ACTIONS",
    "FILE_READ_ACTIONS",
    "ArgvCommand",
    "BoundOutputCursor",
    "BoundOutputReference",
    "BoundProcessHandle",
    "CommandEnvironment",
    "CommandLimits",
    "CommandRequest",
    "DynamicEnvironmentCapability",
    "DynamicEnvironmentConfiguration",
    "EnvironmentAction",
    "EnvironmentActionDispatch",
    "EnvironmentAvailability",
    "EnvironmentChange",
    "EnvironmentConnector",
    "EnvironmentDescriptor",
    "EnvironmentEntry",
    "EnvironmentError",
    "EnvironmentExecution",
    "EnvironmentMount",
    "EnvironmentMountDescriptor",
    "EnvironmentMountInfo",
    "EnvironmentMountObservation",
    "EnvironmentOperationFamily",
    "EnvironmentOutputCapture",
    "EnvironmentOutputPolicy",
    "EnvironmentOutputReadResult",
    "EnvironmentOutputSegment",
    "EnvironmentPath",
    "EnvironmentPermissionSet",
    "EnvironmentReadinessRequirement",
    "EnvironmentRunCallback",
    "EnvironmentRunCallbacks",
    "EnvironmentRunExtension",
    "EnvironmentRunExtensionContext",
    "EnvironmentRunExtensionFactory",
    "EnvironmentRunExtensionFactoryCatalog",
    "EnvironmentRunExtensionFactoryContext",
    "EnvironmentRunExtensionFactoryReference",
    "EnvironmentRunExtensionFactoryRegistration",
    "EnvironmentSnapshot",
    "EnvironmentSource",
    "EnvironmentState",
    "FileArchive",
    "FileBatchValue",
    "FileCopyResult",
    "FileEntriesResult",
    "FileIgnoreMode",
    "FileMetadata",
    "FileMkdirOperation",
    "FileMutationResult",
    "FileOperation",
    "FileOperator",
    "FilePatchResult",
    "FileQueryRequest",
    "FileRemoveOperation",
    "FileScopeSelection",
    "FileStatOperation",
    "FileTextMatch",
    "FileTextResult",
    "FileTextSearchRequest",
    "FileTextSearchResult",
    "FileWriteMode",
    "FileWriteResult",
    "FileWriteTextOperation",
    "OpaqueOutputCursor",
    "OpaqueOutputReference",
    "OpaqueProcessHandle",
    "PortObservation",
    "PortTarget",
    "ProcessControlResult",
    "ProcessDiscovery",
    "ProcessIdentity",
    "ProcessInfo",
    "ProcessOutputSnapshot",
    "ProcessReadOutputResult",
    "ProcessSignalResult",
    "ProcessStartResult",
    "ProcessStatus",
    "ProcessStreamRead",
    "ProcessWriteStdinResult",
    "ShellCommand",
    "ShellExecResult",
    "VirtualFileOperator",
    "build_environment_run_extension_factory_catalog",
    "discover_environment_run_extension_factory_references",
]
