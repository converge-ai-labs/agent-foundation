"""Agent Foundation Harness primary code-first API, loaded on demand."""

import os
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

from a13n_harness._exports import load_export

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

if TYPE_CHECKING:
    from a13n_harness.context import AgentContext, RunBindings
    from a13n_harness.environment import (
        Environment,
        EnvironmentAccess,
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
    )
    from a13n_harness.execution import (
        AgentDefinition,
        DelegationContextPolicy,
        ExecutableAgent,
        HarnessBuilder,
        HarnessRunStream,
        SubagentDefinition,
        SubagentIdentityPolicy,
        derive_child_identity,
    )
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
    from a13n_harness.spec import AgentSpec, HarnessModelCharacteristics, ModelCapability
    from a13n_harness.state import HarnessState
    from a13n_harness.tools.deferred import DeferredToolResume

_EXPORTS = {
    "AgentContext": ("a13n_harness.context", "AgentContext"),
    "RunBindings": ("a13n_harness.context", "RunBindings"),
    "Environment": ("a13n_harness.environment", "Environment"),
    "EnvironmentAccess": ("a13n_harness.environment", "EnvironmentAccess"),
    "EnvironmentEntry": ("a13n_harness.environment", "EnvironmentEntry"),
    "EnvironmentMount": ("a13n_harness.environment", "EnvironmentMount"),
    "DefinitionError": ("a13n_harness.errors", "DefinitionError"),
    "HarnessError": ("a13n_harness.errors", "HarnessError"),
    "IdentityError": ("a13n_harness.errors", "IdentityError"),
    "InputError": ("a13n_harness.errors", "InputError"),
    "ModelResolutionError": ("a13n_harness.errors", "ModelResolutionError"),
    "PluginError": ("a13n_harness.errors", "PluginError"),
    "RunCleanupError": ("a13n_harness.errors", "RunCleanupError"),
    "RunError": ("a13n_harness.errors", "RunError"),
    "StateError": ("a13n_harness.errors", "StateError"),
    "AgentStreamEventProtocol": ("a13n_harness.events", "AgentStreamEventProtocol"),
    "HarnessEvent": ("a13n_harness.events", "HarnessEvent"),
    "HarnessExtensionEvent": ("a13n_harness.events", "HarnessExtensionEvent"),
    "HarnessRunResultEvent": ("a13n_harness.events", "HarnessRunResultEvent"),
    "HarnessStreamEvent": ("a13n_harness.events", "HarnessStreamEvent"),
    "AgentDefinition": ("a13n_harness.execution", "AgentDefinition"),
    "DelegationContextPolicy": ("a13n_harness.execution", "DelegationContextPolicy"),
    "ExecutableAgent": ("a13n_harness.execution", "ExecutableAgent"),
    "HarnessBuilder": ("a13n_harness.execution", "HarnessBuilder"),
    "HarnessRunStream": ("a13n_harness.execution", "HarnessRunStream"),
    "SubagentDefinition": ("a13n_harness.execution", "SubagentDefinition"),
    "SubagentIdentityPolicy": ("a13n_harness.execution", "SubagentIdentityPolicy"),
    "derive_child_identity": ("a13n_harness.execution", "derive_child_identity"),
    "AgentIdentityRef": ("a13n_harness.identity", "AgentIdentityRef"),
    "AgentInstanceContext": ("a13n_harness.identity", "AgentInstanceContext"),
    "AgentInstanceRef": ("a13n_harness.identity", "AgentInstanceRef"),
    "NativeRunInput": ("a13n_harness.input", "NativeRunInput"),
    "RunInputFactory": ("a13n_harness.input", "RunInputFactory"),
    "RunInputValue": ("a13n_harness.input", "RunInputValue"),
    "RunPreparationContext": ("a13n_harness.input", "RunPreparationContext"),
    "SemanticRunInput": ("a13n_harness.input", "SemanticRunInput"),
    "RunModelResolver": ("a13n_harness.models", "RunModelResolver"),
    "infer_model": ("a13n_harness.models", "infer_model"),
    "HarnessInstrumentation": ("a13n_harness.observation", "HarnessInstrumentation"),
    "HarnessObservationContext": ("a13n_harness.observation", "HarnessObservationContext"),
    "HarnessTraceContent": ("a13n_harness.observation", "HarnessTraceContent"),
    "AbstractHarnessPlugin": ("a13n_harness.plugins", "AbstractHarnessPlugin"),
    "PluginOrdering": ("a13n_harness.plugins", "PluginOrdering"),
    "ModelRecoveryPolicy": ("a13n_harness.recovery", "ModelRecoveryPolicy"),
    "ToolRecoveryMode": ("a13n_harness.recovery", "ToolRecoveryMode"),
    "HarnessRunResult": ("a13n_harness.result", "HarnessRunResult"),
    "SafeFailure": ("a13n_harness.result", "SafeFailure"),
    "AgentSpec": ("a13n_harness.spec", "AgentSpec"),
    "HarnessModelCharacteristics": ("a13n_harness.spec", "HarnessModelCharacteristics"),
    "ModelCapability": ("a13n_harness.spec", "ModelCapability"),
    "HarnessState": ("a13n_harness.state", "HarnessState"),
    "DeferredToolResume": ("a13n_harness.tools.deferred", "DeferredToolResume"),
}


def __getattr__(name: str) -> Any:
    return load_export(__name__, globals(), _EXPORTS, name)


__version__ = version("a13n-harness")

__all__ = [
    "AbstractHarnessPlugin",
    "AgentContext",
    "AgentDefinition",
    "AgentIdentityRef",
    "AgentInstanceContext",
    "AgentInstanceRef",
    "AgentSpec",
    "AgentStreamEventProtocol",
    "DeferredToolResume",
    "DefinitionError",
    "DelegationContextPolicy",
    "Environment",
    "EnvironmentAccess",
    "EnvironmentEntry",
    "EnvironmentMount",
    "ExecutableAgent",
    "HarnessBuilder",
    "HarnessError",
    "HarnessEvent",
    "HarnessExtensionEvent",
    "HarnessInstrumentation",
    "HarnessModelCharacteristics",
    "HarnessObservationContext",
    "HarnessRunResult",
    "HarnessRunResultEvent",
    "HarnessRunStream",
    "HarnessState",
    "HarnessStreamEvent",
    "HarnessTraceContent",
    "IdentityError",
    "InputError",
    "ModelCapability",
    "ModelRecoveryPolicy",
    "ModelResolutionError",
    "NativeRunInput",
    "PluginError",
    "PluginOrdering",
    "RunBindings",
    "RunCleanupError",
    "RunError",
    "RunInputFactory",
    "RunInputValue",
    "RunModelResolver",
    "RunPreparationContext",
    "SafeFailure",
    "SemanticRunInput",
    "StateError",
    "SubagentDefinition",
    "SubagentIdentityPolicy",
    "ToolRecoveryMode",
    "__version__",
    "derive_child_identity",
    "infer_model",
]
