"""Anthropic Provider adapter."""

from collections.abc import Mapping
from typing import Any

import httpx2
from anthropic import AsyncAnthropic
from pydantic_ai.providers.anthropic import AnthropicProvider

from .base import (
    JsonModelDiscoveryAdapter,
    ModelListRequest,
    ModelListSchema,
    ProviderIntegration,
    ProviderOperationError,
    join_url,
    require_credential,
    require_endpoint,
)
from .types import ProviderConfiguration, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> AnthropicProvider:
    return AnthropicProvider(
        anthropic_client=AsyncAnthropic(
            api_key=require_credential(provider),
            base_url=require_endpoint(provider),
            http_client=http_client,
            max_retries=0,
            default_headers=provider.extra_headers,
        )
    )


def _request(provider: RuntimeProvider) -> ModelListRequest:
    return ModelListRequest(
        url=join_url(require_endpoint(provider), "v1/models"),
        headers={
            "x-api-key": require_credential(provider),
            "anthropic-version": "2023-06-01",
        },
    )


class AnthropicDiscovery(JsonModelDiscoveryAdapter):
    def next_page(self, payload: Mapping[str, Any]) -> dict[str, str]:
        if payload.get("has_more"):
            last = payload.get("last_id")
            if not isinstance(last, str) or not last:
                raise ProviderOperationError("the Provider omitted its continuation token")
            return {"after_id": last}
        return super().next_page(payload)


INTEGRATION = ProviderIntegration(
    type="anthropic",
    display_name="Anthropic",
    configuration_model=ProviderConfiguration,
    supported_model_apis=("anthropic.messages",),
    build_provider=_build_provider,
    endpoint="https://api.anthropic.com",
    reserved_headers=("x-api-key", "anthropic-version", "anthropic-beta"),
    model_discovery=AnthropicDiscovery(
        request_builder=_request,
        schema=ModelListSchema(
            collection_field="data",
            identifier_field="id",
            display_name_fields=("display_name", "name"),
        ),
    ),
)
