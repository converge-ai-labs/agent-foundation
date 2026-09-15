"""Built-in Connector Providers registered explicitly by the distribution."""

import httpx2

from a13n_service.connectivity.http import EndpointValidator

from ..http import ConnectorHttpClient
from ..registry import ConnectorProviderRegistry


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
    from a13n_service.provider_plugins import load_provider_catalogs
    from a13n_service.provider_plugins.connectors import build_connector_provider_registry

    return build_connector_provider_registry(load_provider_catalogs(()).connector, http)
