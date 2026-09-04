"""Anthropic Provider adapter."""

import httpx2
from pydantic_ai.providers.anthropic import AnthropicProvider

from .base import (
    JsonModelDiscoveryAdapter,
    ModelListRequest,
    ModelListSchema,
    ProviderIntegration,
    join_url,
    require_credential,
    require_endpoint,
)
from .types import EmptyProviderConfiguration, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _pydantic_provider_name: str,
) -> AnthropicProvider:
    return AnthropicProvider(
        api_key=require_credential(provider),
        base_url=provider.endpoint,
        http_client=http_client,
    )


def _request(provider: RuntimeProvider) -> ModelListRequest:
    return ModelListRequest(
        url=join_url(require_endpoint(provider), "v1/models"),
        headers={
            "x-api-key": require_credential(provider),
            "anthropic-version": "2023-06-01",
        },
    )


INTEGRATION = ProviderIntegration(
    type="anthropic",
    display_name="Anthropic",
    configuration_model=EmptyProviderConfiguration,
    supported_model_apis=("anthropic.messages",),
    build_provider=_build_provider,
    endpoint="https://api.anthropic.com",
    model_discovery=JsonModelDiscoveryAdapter(
        request_builder=_request,
        schema=ModelListSchema(
            collection_field="data",
            identifier_field="id",
            display_name_fields=("display_name", "name"),
        ),
    ),
)
