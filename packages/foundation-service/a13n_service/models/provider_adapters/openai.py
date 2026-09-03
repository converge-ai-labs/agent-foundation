"""OpenAI Provider adapter."""

import httpx2
from pydantic_ai.providers.openai import OpenAIProvider

from .base import (
    ProviderIntegration,
    bearer_models_request,
    openai_style_discovery,
    require_credential,
)
from .types import EmptyProviderConfig, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _pydantic_provider_name: str,
) -> OpenAIProvider:
    return OpenAIProvider(
        api_key=require_credential(provider),
        base_url=provider.endpoint,
        http_client=http_client,
    )


INTEGRATION = ProviderIntegration(
    key="openai",
    display_name="OpenAI",
    config_model=EmptyProviderConfig,
    supported_model_apis=("openai.responses", "openai.chat_completions"),
    build_provider=_build_provider,
    endpoint="https://api.openai.com/v1",
    model_discovery=openai_style_discovery(bearer_models_request),
)
