"""Trusted Agent UI composition resolution and immutable snapshot publication."""

from .catalogs import (
    BinderCatalogEntry,
    EnvironmentWorkspaceBinder,
    ProviderCatalogEntry,
    ProviderRuntime,
    ValidatedProfileConfiguration,
    WorkspaceBinderCatalog,
    builtin_workspace_binder_catalog,
)
from .models import (
    DependencyLock,
    ResolvedAgentNode,
    ResolvedAgentSnapshot,
    ResolvedEnvironmentProfile,
    ResolvedMcpRecipe,
    ResolvedModelRecipe,
    ResolvedPluginRecipe,
    ResolvedSubagent,
)
from .reconstruction import AgentReconstructor, PortableCapabilityFactory, ReconstructedAgent
from .resolver import (
    IMPLICIT_NATIVE_PROFILE,
    PACKAGE_PROMPT_VERSION,
    PACKAGE_SYSTEM_PROMPT,
    AgentCompositionResolver,
    ResolvedConfiguration,
)
from .service import AcceptedComposition, CompositionAcceptanceService

__all__ = [
    "IMPLICIT_NATIVE_PROFILE",
    "PACKAGE_PROMPT_VERSION",
    "PACKAGE_SYSTEM_PROMPT",
    "AcceptedComposition",
    "AgentCompositionResolver",
    "AgentReconstructor",
    "BinderCatalogEntry",
    "CompositionAcceptanceService",
    "DependencyLock",
    "EnvironmentWorkspaceBinder",
    "PortableCapabilityFactory",
    "ProviderCatalogEntry",
    "ProviderRuntime",
    "ReconstructedAgent",
    "ResolvedAgentNode",
    "ResolvedAgentSnapshot",
    "ResolvedConfiguration",
    "ResolvedEnvironmentProfile",
    "ResolvedMcpRecipe",
    "ResolvedModelRecipe",
    "ResolvedPluginRecipe",
    "ResolvedSubagent",
    "ValidatedProfileConfiguration",
    "WorkspaceBinderCatalog",
    "builtin_workspace_binder_catalog",
]
