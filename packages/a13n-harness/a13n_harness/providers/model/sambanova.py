"""SambaNova Provider adapter."""

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
) -> SambaNovaProvider:
    from pydantic_ai.providers.sambanova import SambaNovaProvider

    class _SambaNovaProvider(openai_provider.ClientEndpointProvider, SambaNovaProvider):
        pass

    return openai_provider.build(provider, http_client, model_api, _SambaNovaProvider)


DEFINITION = ModelProviderDefinition(
    type="sambanova",
    catalog_providers=("sambanova",),
    setup_url="https://cloud.sambanova.ai/apis",
    display_name="SambaNova",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.sambanova.ai/v1",
    connection_probe=bearer_models_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.sambanova import SambaNovaProvider
