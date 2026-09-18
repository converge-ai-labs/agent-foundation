"""Host-selected Connector definitions with a borrowed process transport."""

from dataclasses import dataclass, field

from a13n_harness.providers.connector import ConnectorProviderCatalog, ConnectorProviderDefinition
from a13n_harness.providers.connector.http import ConnectorHttpClient


@dataclass(frozen=True, slots=True)
class ConnectorProviders:
    catalog: ConnectorProviderCatalog = field(default_factory=ConnectorProviderCatalog)
    http: ConnectorHttpClient | None = None

    def require(self, provider_type: str) -> ConnectorProviderDefinition:
        try:
            return self.catalog[provider_type]
        except KeyError as error:
            raise ValueError("Connector Provider type is not selected") from error
