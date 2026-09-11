"""Built-in Connector Providers registered explicitly by the distribution."""

import httpx2

from a13n_service.connectivity.connectors.providers.configuration import ApiKeyCredentials
from a13n_service.connectivity.http import EndpointValidator

from ..http import ConnectorHttpClient
from ..registry import ConnectorProviderImplementation, ConnectorProviderRegistry
from .composio.configuration import ComposioConfiguration
from .composio.configuration import validate_setup as composio_setup
from .composio.runtime import ComposioProvider


def built_in_connector_provider_registry(
    http_client: httpx2.AsyncClient,
    endpoint_validator: EndpointValidator,
    *,
    response_max_bytes: int,
    timeout_seconds: float = 30,
) -> ConnectorProviderRegistry:
    http = ConnectorHttpClient(
        http_client, endpoint_validator, response_max_bytes=response_max_bytes, timeout_seconds=timeout_seconds
    )
    return ConnectorProviderRegistry(
        (
            ConnectorProviderImplementation(
                type="composio",
                display_name="Composio",
                configuration_model=ComposioConfiguration,
                credential_model=ApiKeyCredentials,
                setup_validator=composio_setup,
                factory=lambda configuration, credentials: ComposioProvider(
                    http,
                    ApiKeyCredentials.model_validate(credentials),
                ),
            ),
        )
    )
