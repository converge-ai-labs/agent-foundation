"""OpenRouter Provider adapter."""

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
    model_api: str,
) -> OpenRouterProvider:
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    from .openai_provider import ClientEndpointProvider, build

    class _OpenRouterProvider(ClientEndpointProvider, OpenRouterProvider):
        pass

    return build(provider, http_client, model_api, _OpenRouterProvider)


def _connection_probe(provider: ModelConnection[ProviderConfiguration, ApiKeyCredential]) -> ConnectionProbeRequest:
    # The public model catalog succeeds even with an invalid API key.
    return ConnectionProbeRequest(
        url=join_url(require_endpoint(provider), "key"),
        headers={"authorization": f"Bearer {require_credential(provider)}"},
    )


DEFINITION = ModelProviderDefinition(
    type="openrouter",
    catalog_providers=("openrouter",),
    setup_url="https://openrouter.ai/settings/keys",
    display_name="OpenRouter",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openrouter.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://openrouter.ai/api/v1",
    connection_probe=_connection_probe,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.openrouter import OpenRouterProvider
