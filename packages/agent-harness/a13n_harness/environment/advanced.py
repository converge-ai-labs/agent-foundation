"""Advanced Environment runtime construction API.

Most embedded applications should pass an EnvironmentProvider or entered
EnvironmentResource directly to ExecutableAgent.run() or stream(). This module
exists for Hosts and provider integrations that need explicit control over a
run-scoped mount set.
"""

from .attachments import create_environment_provider_binding
from .coordinator import (
    CompositeBoundEnvironment,
    EmptyEnvironmentRuntime,
    ManagedEnvironmentRuntime,
    NoopBoundEnvironment,
    create_empty_environment_runtime,
    create_environment_runtime,
)
from .models import EnvironmentRuntimeLimits, EnvironmentStateLimits
from .providers import (
    BoundEnvironment,
    BoundEnvironmentProvider,
    EnvironmentProviderBinding,
    EnvironmentProviderOperations,
    EnvironmentRuntime,
    EnvironmentRuntimeMount,
)

__all__ = [
    "BoundEnvironment",
    "BoundEnvironmentProvider",
    "CompositeBoundEnvironment",
    "EmptyEnvironmentRuntime",
    "EnvironmentProviderBinding",
    "EnvironmentProviderOperations",
    "EnvironmentRuntime",
    "EnvironmentRuntimeLimits",
    "EnvironmentRuntimeMount",
    "EnvironmentStateLimits",
    "ManagedEnvironmentRuntime",
    "NoopBoundEnvironment",
    "create_empty_environment_runtime",
    "create_environment_provider_binding",
    "create_environment_runtime",
]
