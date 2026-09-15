"""Bind inert Connector registrations to one role-owned bounded transport."""

from collections.abc import Iterable

from a13n_service.connectivity.connectors.http import ConnectorHttpClient
from a13n_service.connectivity.connectors.registry import (
    ConnectorProviderImplementation,
    ConnectorProviderRegistry,
)

from .api import ConnectorProviderRegistration


def build_connector_provider_registry(
    registrations: Iterable[ConnectorProviderRegistration],
    http: ConnectorHttpClient,
) -> ConnectorProviderRegistry:
    return ConnectorProviderRegistry(
        (
            ConnectorProviderImplementation(
                type=registration.type,
                display_name=registration.display_name,
                configuration_model=registration.configuration_model,
                credential_model=registration.credential_model,
                setup_validator=registration.setup_validator,
                factory=lambda configuration, credentials, registration=registration: registration.factory(
                    http, configuration, credentials
                ),
            )
            for registration in registrations
        ),
        frozen=True,
    )


__all__ = ["build_connector_provider_registry"]
