"""Shared environment-provider boundary for Agent Foundation agents."""

from importlib.metadata import PackageNotFoundError, version

from .attachments import (
    AcceptedWebSocketEIPSessionSource,
    DirectLocalEnvironmentAttachment,
    EIPEnvironmentAttachment,
    EIPSessionSource,
    EnvironmentRuntimeAttachment,
    HttpEIPSessionSource,
    StdioEIPSessionSource,
)
from .direct_local.configuration import (
    DirectLocalProviderConfiguration,
    DirectLocalProviderStateData,
    DirectLocalRootConfiguration,
    DirectLocalShellProfile,
)
from .direct_local.provider import (
    DirectLocalEnvironmentProvider,
    DirectLocalEnvironmentProviderFactory,
    DirectLocalEnvironmentResource,
    DirectLocalProviderRuntime,
)
from .errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
    EnvironmentProviderSafeError,
)
from .factories import (
    ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP,
    EnvironmentProviderFactory,
    EnvironmentProviderFactoryCatalog,
    EnvironmentProviderFactoryReference,
    EnvironmentProviderFactoryRegistration,
    ResolvedEnvironmentProviderSpec,
    build_environment_provider_factory_catalog,
    discover_environment_provider_factory_references,
)
from .management import EnvironmentProvider, EnvironmentProviderRuntime, EnvironmentResource
from .models import (
    EnvironmentAttachmentConcurrency,
    EnvironmentLifecycleCapabilities,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    EnvironmentProviderResourceState,
    EnvironmentProviderSpec,
    EnvironmentReconciliationPhase,
    EnvironmentReconciliationResult,
    EnvironmentResourceAllocation,
)

try:
    __version__ = version("a13n-environment-provider")
except PackageNotFoundError:  # pragma: no cover - source-tree imports without installation
    __version__ = "0.0.0"

__all__ = [
    "ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP",
    "AcceptedWebSocketEIPSessionSource",
    "DirectLocalEnvironmentAttachment",
    "DirectLocalEnvironmentProvider",
    "DirectLocalEnvironmentProviderFactory",
    "DirectLocalEnvironmentResource",
    "DirectLocalProviderConfiguration",
    "DirectLocalProviderRuntime",
    "DirectLocalProviderStateData",
    "DirectLocalRootConfiguration",
    "DirectLocalShellProfile",
    "EIPEnvironmentAttachment",
    "EIPSessionSource",
    "EnvironmentAttachmentConcurrency",
    "EnvironmentLifecycleCapabilities",
    "EnvironmentManagementAction",
    "EnvironmentOperationContext",
    "EnvironmentPauseMode",
    "EnvironmentProvider",
    "EnvironmentProviderError",
    "EnvironmentProviderErrorCategory",
    "EnvironmentProviderErrorContext",
    "EnvironmentProviderFactory",
    "EnvironmentProviderFactoryCatalog",
    "EnvironmentProviderFactoryReference",
    "EnvironmentProviderFactoryRegistration",
    "EnvironmentProviderOutcomeCertainty",
    "EnvironmentProviderRecoveryHint",
    "EnvironmentProviderResourceState",
    "EnvironmentProviderRuntime",
    "EnvironmentProviderSafeError",
    "EnvironmentProviderSpec",
    "EnvironmentReconciliationPhase",
    "EnvironmentReconciliationResult",
    "EnvironmentResource",
    "EnvironmentResourceAllocation",
    "EnvironmentRuntimeAttachment",
    "HttpEIPSessionSource",
    "ResolvedEnvironmentProviderSpec",
    "StdioEIPSessionSource",
    "__version__",
    "build_environment_provider_factory_catalog",
    "discover_environment_provider_factory_references",
]
