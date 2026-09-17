"""Moonshot Provider adapter."""

import httpx2
from pydantic_ai.providers.moonshotai import MoonshotAIProvider

from . import openai_provider
from .base import ProviderIntegration, bearer_models_request
from .types import ProviderConfiguration, RuntimeProvider


class _MoonshotAIProvider(openai_provider.ClientEndpointProvider, MoonshotAIProvider):
    pass


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> MoonshotAIProvider:
    return openai_provider.build(provider, http_client, model_api, _MoonshotAIProvider)


INTEGRATION = ProviderIntegration(
    type="moonshot",
    display_name="Moonshot / Kimi",
    configuration_model=ProviderConfiguration,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.moonshot.cn/v1",
    connection_probe=bearer_models_request,
)
