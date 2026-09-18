"""Built-in Providers registered through the deployment extension contract."""

from __future__ import annotations

from a13n_harness.memory_plugins import Mem0OSSBackendPlugin, Mem0PlatformBackendPlugin

from a13n_service.connectivity.connectors.providers.composio.configuration import (
    ComposioConfiguration,
    validate_setup,
)
from a13n_service.connectivity.connectors.providers.composio.runtime import ComposioProvider
from a13n_service.connectivity.connectors.providers.configuration import ApiKeyCredentials

from .api import ConnectorProviderRegistration, ProviderPluginRegistry


def register(registry: ProviderPluginRegistry) -> None:
    registry.memory.register(Mem0OSSBackendPlugin())
    registry.memory.register(Mem0PlatformBackendPlugin())
    registry.connector.register(
        ConnectorProviderRegistration(
            type="composio",
            display_name="Composio",
            configuration_model=ComposioConfiguration,
            credential_model=ApiKeyCredentials,
            setup_validator=validate_setup,
            factory=lambda http, configuration, credentials: ComposioProvider(
                http,
                ApiKeyCredentials.model_validate(credentials),
            ),
        )
    )
