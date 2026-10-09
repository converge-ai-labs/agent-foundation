"""Agent Foundation Harness primary code-first API, loaded on demand."""
# ruff: noqa: F401  # `_EXPORTS` owns the surface; these imports serve type checkers.

import os
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

from a13n_harness._exports import exported_names, load_export

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

if TYPE_CHECKING:
    from a13n_harness.builder import (
        AgentDefinition,
        DelegationContextPolicy,
        HarnessBuilder,
        SubagentDefinition,
        SubagentIdentityPolicy,
        derive_child_identity,
    )
    from a13n_harness.configuration import RunConfiguration
    from a13n_harness.content import ContentItem, ContentMetadata
    from a13n_harness.context import AgentContext, RunBindings
    from a13n_harness.environment import (
        EnvironmentConnector,
        EnvironmentEntry,
        EnvironmentMount,
    )
    from a13n_harness.errors import (
        DefinitionError,
        HarnessError,
        IdentityError,
        InputError,
        ModelResolutionError,
        PluginError,
        RunCleanupError,
        RunError,
        StateError,
    )
    from a13n_harness.events import (
        AgentStreamEventProtocol,
        HarnessEvent,
        HarnessExtensionEvent,
        HarnessRunResultEvent,
        HarnessStreamEvent,
        InputMediaEvent,
        InputTextEvent,
    )
    from a13n_harness.execution import ExecutableAgent, HarnessRunStream
    from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext, AgentInstanceRef
    from a13n_harness.input import (
        NativeRunInput,
        RunInputFactory,
        RunInputValue,
        RunPreparationContext,
        SemanticRunInput,
    )
    from a13n_harness.models import RunModelResolver, infer_model
    from a13n_harness.observation import (
        HarnessInstrumentation,
        HarnessObservationContext,
        HarnessTraceContent,
    )
    from a13n_harness.plugins import AbstractHarnessPlugin, PluginOrdering
    from a13n_harness.recovery import ModelRecoveryPolicy, ToolRecoveryMode
    from a13n_harness.result import HarnessRunResult, SafeFailure
    from a13n_harness.spec import (
        AgentSpec,
        HarnessModelCharacteristics,
        ImageInputPolicy,
        ModelCapability,
        UrlInputSupport,
        VideoInputPolicy,
        VideoUrlType,
    )
    from a13n_harness.state import HarnessState, StateStore, StoredRef
    from a13n_harness.tools.deferred import DeferredToolResume

_EXPORTS = {
    "a13n_harness.configuration": ("RunConfiguration",),
    "a13n_harness.context": (
        "AgentContext",
        "RunBindings",
    ),
    "a13n_harness.content": ("ContentItem", "ContentMetadata"),
    "a13n_harness.environment": (
        "EnvironmentConnector",
        "EnvironmentEntry",
        "EnvironmentMount",
    ),
    "a13n_harness.errors": (
        "DefinitionError",
        "HarnessError",
        "IdentityError",
        "InputError",
        "ModelResolutionError",
        "PluginError",
        "RunCleanupError",
        "RunError",
        "StateError",
    ),
    "a13n_harness.events": (
        "AgentStreamEventProtocol",
        "HarnessEvent",
        "HarnessExtensionEvent",
        "InputMediaEvent",
        "InputTextEvent",
        "HarnessRunResultEvent",
        "HarnessStreamEvent",
    ),
    "a13n_harness.builder": (
        "AgentDefinition",
        "DelegationContextPolicy",
        "HarnessBuilder",
        "SubagentDefinition",
        "SubagentIdentityPolicy",
        "derive_child_identity",
    ),
    "a13n_harness.execution": (
        "ExecutableAgent",
        "HarnessRunStream",
    ),
    "a13n_harness.identity": (
        "AgentIdentityRef",
        "AgentInstanceContext",
        "AgentInstanceRef",
    ),
    "a13n_harness.input": (
        "NativeRunInput",
        "RunInputFactory",
        "RunInputValue",
        "RunPreparationContext",
        "SemanticRunInput",
    ),
    "a13n_harness.models": (
        "RunModelResolver",
        "infer_model",
    ),
    "a13n_harness.observation": (
        "HarnessInstrumentation",
        "HarnessObservationContext",
        "HarnessTraceContent",
    ),
    "a13n_harness.plugins": (
        "AbstractHarnessPlugin",
        "PluginOrdering",
    ),
    "a13n_harness.recovery": (
        "ModelRecoveryPolicy",
        "ToolRecoveryMode",
    ),
    "a13n_harness.result": (
        "HarnessRunResult",
        "SafeFailure",
    ),
    "a13n_harness.spec": (
        "AgentSpec",
        "HarnessModelCharacteristics",
        "ImageInputPolicy",
        "ModelCapability",
        "UrlInputSupport",
        "VideoInputPolicy",
        "VideoUrlType",
    ),
    "a13n_harness.state": ("HarnessState", "StateStore", "StoredRef"),
    "a13n_harness.tools.deferred": ("DeferredToolResume",),
}


def __getattr__(name: str) -> Any:
    return load_export(__name__, globals(), _EXPORTS, name)


__version__ = version("a13n-harness")

__all__ = [*exported_names(_EXPORTS), "__version__"]  # pyright: ignore[reportUnsupportedDunderAll]
