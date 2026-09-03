"""Distribution-owned built-in Ingress adapter registry."""

from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry

from .slack import SlackIngressAdapter


def built_in_ingress_adapter_registry() -> AdapterRegistry[IngressAdapter]:
    return AdapterRegistry(
        (
            AdapterDefinition[IngressAdapter](
                key=SlackIngressAdapter.provider_key,
                config_versions=SlackIngressAdapter.config_versions,
                factory=SlackIngressAdapter,
            ),
        )
    )
