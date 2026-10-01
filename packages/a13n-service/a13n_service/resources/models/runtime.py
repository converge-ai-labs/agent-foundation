"""Attempt-owned model clients: the provider's secrets are revealed only here, under the endpoint policy and bounds."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic_ai.models import Model

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http
from a13n_service.providers.registry import Registry
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.resources.providers.oauth import ChatGPTCredentialSource
from a13n_service.settings import Providers


@asynccontextmanager
async def open_model(
    model: ResolvedModel,
    *,
    registry: Registry,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Providers,
    storage: Storage | None = None,
) -> AsyncIterator[Model]:
    """The native model the Harness builds for this model's provider, entered; the caller owns its lifetime."""
    definition = registry.get("model", model.provider.type)
    credential = model.provider.reveal_credential(keys)
    source = None
    if definition.oauth is not None:
        if storage is None or model.provider.location.organization_id is None:
            raise ServiceError(
                "unavailable", "Model Provider OAuth storage is unavailable", {"dependency": "model_oauth"}
            )
        source = ChatGPTCredentialSource(storage, keys, model.provider.id, model.provider.location.organization_id)
    async with open_http(policy, timeout=settings.model_timeout, max_bytes=settings.response_bytes) as client:
        native = await definition.build(
            model.config.model_name,
            configuration=model.provider.config,
            credential=credential,
            credential_source=source,
            model_api=model.config.model_api,
            http_client=client,
            extra_headers=model.provider.reveal_headers(keys),
            endpoint_policy=policy,
        )
        async with native:
            yield native
