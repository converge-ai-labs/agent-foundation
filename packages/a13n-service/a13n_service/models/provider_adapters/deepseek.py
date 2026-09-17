"""DeepSeek Provider adapter."""

import httpx2
from pydantic_ai.providers.deepseek import DeepSeekProvider

from . import openai_provider
from .base import ProviderIntegration, bearer_models_request
from .types import ProviderConfiguration, RuntimeProvider


class _DeepSeekProvider(openai_provider.ClientEndpointProvider, DeepSeekProvider):
    pass


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> DeepSeekProvider:
    return openai_provider.build(provider, http_client, model_api, _DeepSeekProvider)


INTEGRATION = ProviderIntegration(
    type="deepseek",
    display_name="DeepSeek",
    configuration_model=ProviderConfiguration,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.deepseek.com",
    connection_probe=bearer_models_request,
)
