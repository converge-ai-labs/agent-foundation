"""DeepSeek Provider adapter."""

import httpx2
from pydantic_ai.providers.deepseek import DeepSeekProvider

from . import openai_provider
from .base import ProviderIntegration, bearer_models_request
from .openai_provider import openai_style_discovery
from .types import EmptyProviderConfiguration, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    pydantic_provider_name: str,
) -> DeepSeekProvider:
    return openai_provider.build(provider, http_client, pydantic_provider_name, DeepSeekProvider)


INTEGRATION = ProviderIntegration(
    type="deepseek",
    display_name="DeepSeek",
    configuration_model=EmptyProviderConfiguration,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.deepseek.com",
    model_discovery=openai_style_discovery(bearer_models_request),
)
