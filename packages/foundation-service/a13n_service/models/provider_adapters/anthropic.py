"""Anthropic Provider adapter."""

import httpx2
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from ..domain import ModelExecutionSnapshot
from .base import (
    BuiltModel,
    JsonModelDiscoveryAdapter,
    ModelListRequest,
    ModelListSchema,
    ProviderAdapter,
    join_url,
    model_name,
    require_api,
    require_credential,
    require_endpoint,
)
from .types import EmptyProviderConfig, ProviderType, RuntimeProvider


def _build(snapshot: ModelExecutionSnapshot, provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> BuiltModel:
    require_api(snapshot, "anthropic.messages")
    return AnthropicModel(
        model_name(snapshot),
        provider=AnthropicProvider(
            api_key=require_credential(provider),
            base_url=provider.endpoint,
            http_client=http_client,
        ),
    )


def _request(provider: RuntimeProvider) -> ModelListRequest:
    return ModelListRequest(
        url=join_url(require_endpoint(provider), "v1/models"),
        headers={
            "x-api-key": require_credential(provider),
            "anthropic-version": "2023-06-01",
        },
    )


ADAPTER = ProviderAdapter(
    build_model=_build,
    model_discovery=JsonModelDiscoveryAdapter(
        request_builder=_request,
        schema=ModelListSchema(
            collection_field="data",
            identifier_field="id",
            display_name_fields=("display_name", "name"),
        ),
    ),
)

TYPE = ProviderType(
    key="anthropic",
    display_name="Anthropic",
    config_model=EmptyProviderConfig,
    supported_model_apis=("anthropic.messages",),
    endpoint="https://api.anthropic.com",
    supports_model_discovery=True,
)
