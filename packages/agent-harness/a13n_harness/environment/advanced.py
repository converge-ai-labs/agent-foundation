"""Advanced Environment binding and dynamic-topology construction API.

Most embedded applications should pass an EnvironmentProvider or entered
EnvironmentResource directly to ExecutableAgent.run() or stream(). This module
exists for Hosts and provider integrations that need to construct or replace
run-scoped topology explicitly.
"""

from .attachments import create_environment_provider_binding
from .coordinator import (
    CompositeBoundEnvironment,
    CompositeEnvironmentRunBinding,
    NoopBoundEnvironment,
    NoopEnvironmentRunBinding,
    create_environment_run_binding,
    create_noop_environment_run_binding,
)
from .models import (
    EnvironmentBindingRequest,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
)
from .providers import (
    BoundEnvironment,
    BoundEnvironmentProvider,
    EnvironmentProviderBinding,
    EnvironmentProviderOperations,
    EnvironmentRunBinding,
    EnvironmentTopologyController,
    EnvironmentTopologyObserver,
)

__all__ = [
    "BoundEnvironment",
    "BoundEnvironmentProvider",
    "CompositeBoundEnvironment",
    "CompositeEnvironmentRunBinding",
    "EnvironmentBindingRequest",
    "EnvironmentProviderBinding",
    "EnvironmentProviderOperations",
    "EnvironmentRunBinding",
    "EnvironmentStateLimits",
    "EnvironmentTopologyController",
    "EnvironmentTopologyLimits",
    "EnvironmentTopologyObserver",
    "EnvironmentTopologyRequest",
    "NoopBoundEnvironment",
    "NoopEnvironmentRunBinding",
    "create_environment_provider_binding",
    "create_environment_run_binding",
    "create_noop_environment_run_binding",
]
