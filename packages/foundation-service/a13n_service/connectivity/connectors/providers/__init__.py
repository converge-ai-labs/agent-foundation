"""Built-in Connector adapter registration."""

from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.connectivity.connectors.adapters import ConnectorAdapter
from a13n_service.connectivity.http import EndpointValidator

from .composio import ComposioAdapter
from .openconnector import OpenConnectorAdapter


def built_in_connector_adapter_registry(
    http_client,
    endpoint_validator: EndpointValidator,
    *,
    response_max_bytes: int,
) -> AdapterRegistry[ConnectorAdapter]:
    return AdapterRegistry(
        (
            AdapterDefinition[ConnectorAdapter](
                key=OpenConnectorAdapter.driver_key,
                config_versions=OpenConnectorAdapter.config_versions,
                factory=lambda: OpenConnectorAdapter(
                    http_client,
                    endpoint_validator,
                    response_max_bytes=response_max_bytes,
                ),
            ),
            AdapterDefinition[ConnectorAdapter](
                key=ComposioAdapter.driver_key,
                config_versions=ComposioAdapter.config_versions,
                factory=lambda: ComposioAdapter(
                    http_client,
                    endpoint_validator,
                    response_max_bytes=response_max_bytes,
                ),
            ),
        )
    )
