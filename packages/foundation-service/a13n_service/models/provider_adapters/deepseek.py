"""DeepSeek Provider adapter."""

import httpx2
from pydantic_ai.providers.deepseek import DeepSeekProvider

from . import openai_provider
from .base import ProviderIntegration, bearer_models_request, openai_style_discovery
from .types import EmptyProviderConfig, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    pydantic_provider_name: str,
) -> DeepSeekProvider:
    return openai_provider.build(provider, http_client, pydantic_provider_name, DeepSeekProvider)


INTEGRATION = ProviderIntegration(
    key="deepseek",
    display_name="DeepSeek",
    config_model=EmptyProviderConfig,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.deepseek.com",
    model_discovery=openai_style_discovery(bearer_models_request),
)
