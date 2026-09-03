"""Distribution-owned built-in Ingress adapter registry."""

from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry

from .common.origins import normalize_provider_origins
from .github import GitHubIngressAdapter
from .lark import LarkIngressAdapter
from .slack import SlackIngressAdapter


def built_in_ingress_adapter_registry(
    *,
    allowed_provider_origins: tuple[str, ...] = (),
) -> AdapterRegistry[IngressAdapter]:
    normalized_origins = tuple(normalize_provider_origins(allowed_provider_origins))
    return AdapterRegistry(
        (
            AdapterDefinition[IngressAdapter](
                key=SlackIngressAdapter.provider_key,
                config_versions=SlackIngressAdapter.config_versions,
                factory=SlackIngressAdapter,
            ),
            AdapterDefinition[IngressAdapter](
                key=LarkIngressAdapter.provider_key,
                config_versions=LarkIngressAdapter.config_versions,
                factory=lambda: LarkIngressAdapter(
                    allowed_provider_origins=normalized_origins,
                ),
            ),
            AdapterDefinition[IngressAdapter](
                key=GitHubIngressAdapter.provider_key,
                config_versions=GitHubIngressAdapter.config_versions,
                factory=lambda: GitHubIngressAdapter(
                    allowed_provider_origins=normalized_origins,
                ),
            ),
        )
    )
