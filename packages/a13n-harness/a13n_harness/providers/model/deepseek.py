"""DeepSeek Provider adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2

from . import openai_provider
from .credentials import ApiKeyCredential
from .definition import ModelProviderDefinition, bearer_models_request
from .types import ModelConnection, ProviderConfiguration


def _build_provider(
    provider: ModelConnection[ProviderConfiguration, ApiKeyCredential],
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> DeepSeekProvider:
    from pydantic_ai.providers.deepseek import DeepSeekProvider

    class _DeepSeekProvider(openai_provider.ClientEndpointProvider, DeepSeekProvider):
        pass

    return openai_provider.build(provider, http_client, model_api, _DeepSeekProvider)


DEFINITION = ModelProviderDefinition(
    type="deepseek",
    catalog_providers=("deepseek",),
    setup_url="https://platform.deepseek.com/api_keys",
    display_name="DeepSeek",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.deepseek.com",
    connection_probe=bearer_models_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.deepseek import DeepSeekProvider
