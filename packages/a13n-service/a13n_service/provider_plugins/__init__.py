"""Public extension API for explicitly selected deployment Provider packages."""

from a13n_environment import EnvironmentProvider

from a13n_service.connectivity.connectors.contracts import ConnectorProviderRuntime
from a13n_service.models.provider_adapters.base import ProviderIntegration
from a13n_service.models.provider_adapters.types import (
    CredentialFormat,
    ProviderConfiguration,
    RuntimeProvider,
)

from .api import (
    PROVIDER_EXTENSION_API_VERSION,
    ConnectorProviderRegistration,
    ProviderPluginRegistry,
    WebProviderRegistration,
    provider_plugin,
)
from .catalog import ProviderCatalogs, ProviderPluginError, load_provider_catalogs

__all__ = [
    "PROVIDER_EXTENSION_API_VERSION",
    "ConnectorProviderRegistration",
    "ConnectorProviderRuntime",
    "CredentialFormat",
    "EnvironmentProvider",
    "ProviderCatalogs",
    "ProviderConfiguration",
    "ProviderIntegration",
    "ProviderPluginError",
    "ProviderPluginRegistry",
    "RuntimeProvider",
    "WebProviderRegistration",
    "load_provider_catalogs",
    "provider_plugin",
]
