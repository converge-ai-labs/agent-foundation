"""Agent Foundation Harness primary code-first API."""

import os
from importlib.metadata import version

# Suppress the upstream banner by default before importing Pydantic AI.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from a13n_harness.context import AgentContext, RunBindings
from a13n_harness.environment import (
    Environment,
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
