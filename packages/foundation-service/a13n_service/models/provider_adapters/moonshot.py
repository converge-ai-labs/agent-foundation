"""Moonshot Provider adapter."""

import httpx2
from pydantic_ai.providers.moonshotai import MoonshotAIProvider

from . import openai_provider
from .base import ProviderIntegration, bearer_models_request
from .openai_provider import openai_style_discovery
from .types import EmptyProviderConfiguration, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    pydantic_provider_name: str,
) -> MoonshotAIProvider:
    return openai_provider.build(provider, http_client, pydantic_provider_name, MoonshotAIProvider)


INTEGRATION = ProviderIntegration(
    type="moonshot",
    display_name="Moonshot / Kimi",
    configuration_model=EmptyProviderConfiguration,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.moonshot.cn/v1",
    model_discovery=openai_style_discovery(bearer_models_request),
)
