"""OpenRouter Provider adapter."""

import httpx2
from pydantic_ai.providers.openrouter import OpenRouterProvider

from .base import (
    ProviderIntegration,
    bearer_models_request,
    openai_style_discovery,
    require_credential,
)
from .types import EmptyProviderConfiguration, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _pydantic_provider_name: str,
) -> OpenRouterProvider:
    return OpenRouterProvider(api_key=require_credential(provider), http_client=http_client)


INTEGRATION = ProviderIntegration(
    type="openrouter",
    display_name="OpenRouter",
    configuration_model=EmptyProviderConfiguration,
    supported_model_apis=("openrouter.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://openrouter.ai/api/v1",
    model_discovery=openai_style_discovery(bearer_models_request),
)
