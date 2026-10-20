"""Harness UI accepted-generation resolution and per-Run composition."""

__lazy_modules__ = [
    "a13n_harness_ui.composition.models",
    "a13n_harness_ui.composition.reconstruction",
    "a13n_harness_ui.composition.resolver",
    "a13n_harness_ui.composition.service",
]

from .models import (
    DependencyProvenance,
    ResolvedAgentNode,
    ResolvedCapabilityRecipe,
    ResolvedContentPlugin,
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
    "ResolvedContentPlugin",
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
