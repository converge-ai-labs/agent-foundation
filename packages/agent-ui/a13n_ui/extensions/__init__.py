"""Capability and installed-extension discovery for Agent UI."""

from .catalog import (
    AgentUiExtensionCatalog,
    CatalogReference,
    ImplementationReference,
    SelectedCapability,
)
from .environment_adapters import (
    LOCAL_ENVD_ADAPTER_KEY,
    LOCAL_ENVD_PROVIDER_KEY,
    NATIVE_ADAPTER_KEY,
    NATIVE_PROVIDER_KEY,
    EnvironmentProjectAdapter,
    LocalEnvdProjectAdapter,
    NativeProjectAdapter,
)

__all__ = [
    "LOCAL_ENVD_ADAPTER_KEY",
    "LOCAL_ENVD_PROVIDER_KEY",
    "NATIVE_ADAPTER_KEY",
    "NATIVE_PROVIDER_KEY",
    "AgentUiExtensionCatalog",
    "CatalogReference",
    "EnvironmentProjectAdapter",
    "ImplementationReference",
    "LocalEnvdProjectAdapter",
    "NativeProjectAdapter",
    "SelectedCapability",
]
