"""OpenRouter Provider adapter."""

import httpx2
from pydantic_ai.providers.openrouter import OpenRouterProvider

from .base import (
    ProviderIntegration,
    bearer_models_request,
)
from .openai_provider import ClientEndpointProvider, build
from .types import ProviderConfiguration, RuntimeProvider


class _OpenRouterProvider(ClientEndpointProvider, OpenRouterProvider):
    pass


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> OpenRouterProvider:
    return build(provider, http_client, model_api, _OpenRouterProvider)


INTEGRATION = ProviderIntegration(
    type="openrouter",
    display_name="OpenRouter",
    configuration_model=ProviderConfiguration,
    supported_model_apis=("openrouter.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://openrouter.ai/api/v1",
    connection_probe=bearer_models_request,
)
