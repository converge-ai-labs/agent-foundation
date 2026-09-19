"""Provider-neutral run-scoped Environment API."""

from typing import TYPE_CHECKING, Any

from a13n_environment import Environment

from .commands import (
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
from .files import (
    FileCopyResult,
    FileEntriesResult,
    FileIgnoreMode,
    FileMetadata,
    FileMutationResult,
    FileOperator,
    FilePatchResult,
    FileQueryRequest,
    FileTextMatch,
    FileTextResult,
    FileTextSearchRequest,
    FileTextSearchResult,
    FileWriteMode,
    FileWriteResult,
)
from .models import (
    DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS,
    ENVIRONMENT_ACTION_CATALOG_VERSION,
    ENVIRONMENT_ACTION_DISPATCH,
    EnvironmentAction,
    EnvironmentActionDispatch,
    EnvironmentAvailability,
    EnvironmentChange,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentMountDescriptor,
    EnvironmentMountInfo,
    EnvironmentMountObservation,
    EnvironmentOperationFamily,
    EnvironmentPath,
    EnvironmentPermissionSet,
    EnvironmentReadinessRequirement,
    EnvironmentSnapshot,
    EnvironmentState,
)
from .providers import FileScopeSelection
from .retention import (
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
from .sources import EnvironmentEntry, EnvironmentMount
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
    "ArgvCommand",
    "BoundOutputCursor",
    "BoundOutputReference",
    "BoundProcessHandle",
    "CommandEnvironment",
    "CommandLimits",
    "CommandRequest",
    "DynamicEnvironmentCapability",
    "DynamicEnvironmentConfiguration",
    "Environment",
    "EnvironmentAction",
    "EnvironmentActionDispatch",
    "EnvironmentAvailability",
    "EnvironmentChange",
    "EnvironmentDescriptor",
    "EnvironmentEntry",
    "EnvironmentError",
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
    "EnvironmentState",
    "FileCopyResult",
    "FileEntriesResult",
    "FileIgnoreMode",
    "FileMetadata",
    "FileMutationResult",
    "FileOperator",
    "FilePatchResult",
    "FileQueryRequest",
    "FileScopeSelection",
    "FileTextMatch",
    "FileTextResult",
    "FileTextSearchRequest",
    "FileTextSearchResult",
    "FileWriteMode",
    "FileWriteResult",
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
