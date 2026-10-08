"""Authorized provider model choices, fetched outside the storage boundary."""

import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.model.definition import DiscoveredModel, ProviderOperationError
from anyio import fail_after, to_thread

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError, invalid
from a13n_service.infra.outbound import open_http
from a13n_service.providers.registry import Registry
from a13n_service.resources.models.catalog import ModelsDevCatalog, model_characteristics
from a13n_service.resources.models.schemas import ModelCatalog
from a13n_service.resources.providers.oauth import ChatGPTCredentialSource
from a13n_service.resources.providers.schemas import ProviderModel
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import ModelProviderRow
from a13n_service.settings import Providers
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


async def discover_models(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    provider_id: str,
    *,
    registry: Registry,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Providers,
    catalog: ModelsDevCatalog | None = None,
) -> list[ProviderModel]:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        provider = await resolve_provider(session, actor, ModelProviderRow, scope, provider_id)
    definition = registry.get("model", provider.type)
    if not definition.supports_model_discovery:
        raise invalid("provider_id", "This provider does not support model discovery")
    source = None
    if definition.oauth is not None:
        assert provider.location.organization_id is not None
        source = ChatGPTCredentialSource(storage, keys, provider.id, provider.location.organization_id)
    try:
        with fail_after(settings.model_timeout):
            async with open_http(policy, timeout=settings.model_timeout, max_bytes=settings.response_bytes) as client:
                models = await definition.discover_models(
                    configuration=provider.config,
                    credential=provider.reveal_credential(keys),
                    credential_source=source,
                    http_client=client,
                    extra_headers=provider.reveal_headers(keys),
                    endpoint_policy=policy,
                )
    except (TimeoutError, httpx2.HTTPError, ProviderOperationError, ValueError):
        # Provider bodies and validation inputs may contain secrets. Never relay them to the caller.
        raise ServiceError(
            "unavailable", "Provider model discovery failed; retry or enter a model ID manually"
        ) from None
    # Metadata availability must never delay or replace authenticated choices.
    metadata = await catalog.read(wait=False) if catalog is not None else ModelCatalog(items=[], status="unavailable")
    # The bundled fallback lazily reads YAML and builds the pricing snapshot.
    return await to_thread.run_sync(_choices, models, definition.catalog_providers, metadata)


def _choices(
    models: tuple[DiscoveredModel, ...], channels: tuple[str, ...], metadata: ModelCatalog
) -> list[ProviderModel]:
    return [
        ProviderModel(
            slug=model.model_name,
            display_name=model.display_name,
            characteristics=model_characteristics(channels, model.model_name, metadata),
        )
        for model in models
    ]
