"""Agent UI accepted-generation resolution and per-Run composition."""

from .models import (
    DependencyProvenance,
    ResolvedAgentNode,
    ResolvedCapabilityRecipe,
    ResolvedEnvironmentProfile,
    ResolvedMcpRecipe,
    ResolvedModelRecipe,
    ResolvedPluginRecipe,
    ResolvedRunComposition,
    ResolvedRunExtensionRecipe,
    ResolvedSubagent,
)
from .reconstruction import AgentReconstructor, ReconstructedAgent
from .resolver import (
    IMPLICIT_NATIVE_PROFILE,
    PACKAGE_PROMPT_REVISION,
    PACKAGE_SYSTEM_PROMPT,
    AgentCompositionResolver,
    ThreadCompositionSelection,
)
from .service import (
    AcceptedComposition,
    CompositionAcceptanceService,
    PublishedRunComposition,
    RunCompositionService,
)

__all__ = [
    "IMPLICIT_NATIVE_PROFILE",
    "PACKAGE_PROMPT_REVISION",
    "PACKAGE_SYSTEM_PROMPT",
    "AcceptedComposition",
    "AgentCompositionResolver",
    "AgentReconstructor",
    "CompositionAcceptanceService",
    "DependencyProvenance",
    "PublishedRunComposition",
    "ReconstructedAgent",
    "ResolvedAgentNode",
    "ResolvedCapabilityRecipe",
    "ResolvedEnvironmentProfile",
    "ResolvedMcpRecipe",
    "ResolvedModelRecipe",
    "ResolvedPluginRecipe",
    "ResolvedRunComposition",
    "ResolvedRunExtensionRecipe",
    "ResolvedSubagent",
    "RunCompositionService",
    "ThreadCompositionSelection",
]
