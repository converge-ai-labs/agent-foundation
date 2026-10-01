"""Together AI Provider adapter."""

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
) -> TogetherProvider:
    from pydantic_ai.providers.together import TogetherProvider

    class _TogetherProvider(openai_provider.ClientEndpointProvider, TogetherProvider):
        pass

    return openai_provider.build(provider, http_client, model_api, _TogetherProvider)


DEFINITION = ModelProviderDefinition(
    type="together",
    catalog_providers=("togetherai",),
    setup_url="https://api.together.ai/settings/api-keys",
    display_name="Together AI",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.together.xyz/v1",
    connection_probe=bearer_models_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.together import TogetherProvider
