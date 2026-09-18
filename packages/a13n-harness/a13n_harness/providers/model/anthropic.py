"""Anthropic Provider adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2

from .credentials import ApiKeyCredential
from .definition import (
    ConnectionProbeRequest,
    ModelProviderDefinition,
    join_url,
    require_credential,
    require_endpoint,
)
from .types import ModelConnection, ProviderConfiguration


def _build_provider(
    provider: ModelConnection[ProviderConfiguration, ApiKeyCredential],
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> AnthropicProvider:
    from anthropic import AsyncAnthropic
    from pydantic_ai.providers.anthropic import AnthropicProvider

    return AnthropicProvider(
        anthropic_client=AsyncAnthropic(
            api_key=require_credential(provider),
            base_url=require_endpoint(provider),
            http_client=http_client,
            max_retries=0,
            default_headers=provider.extra_headers,
        )
    )


def _request(provider: ModelConnection[ProviderConfiguration, ApiKeyCredential]) -> ConnectionProbeRequest:
    return ConnectionProbeRequest(
        url=join_url(require_endpoint(provider), "v1/models"),
        headers={
            "x-api-key": require_credential(provider),
            "anthropic-version": "2023-06-01",
        },
    )


DEFINITION = ModelProviderDefinition(
    type="anthropic",
    setup_url="https://platform.claude.com/settings/keys",
    display_name="Anthropic",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("anthropic.messages",),
    build_provider=_build_provider,
    endpoint="https://api.anthropic.com",
    reserved_headers=("x-api-key", "anthropic-version", "anthropic-beta"),
    connection_probe=_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.anthropic import AnthropicProvider
