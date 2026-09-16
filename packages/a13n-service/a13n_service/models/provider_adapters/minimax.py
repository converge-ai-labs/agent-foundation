"""MiniMax's OpenAI-compatible Chat Completions connection."""

import httpx2
from pydantic_ai.providers.openai import OpenAIProvider

from . import openai_provider
from .base import ProviderIntegration, bearer_models_request
from .types import ProviderConfiguration, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> OpenAIProvider:
    return openai_provider.build(provider, http_client, model_api, OpenAIProvider)


INTEGRATION = ProviderIntegration(
    type="minimax",
    display_name="MiniMax",
    configuration_model=ProviderConfiguration,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.minimax.io/v1",
    connection_probe=bearer_models_request,
)
