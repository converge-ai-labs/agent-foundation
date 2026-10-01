"""Fireworks AI Provider adapter."""

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
) -> FireworksProvider:
    from pydantic_ai.providers.fireworks import FireworksProvider

    class _FireworksProvider(openai_provider.ClientEndpointProvider, FireworksProvider):
        pass

    return openai_provider.build(provider, http_client, model_api, _FireworksProvider)


DEFINITION = ModelProviderDefinition(
    type="fireworks",
    catalog_providers=("fireworks-ai",),
    setup_url="https://app.fireworks.ai/settings/users/api-keys",
    display_name="Fireworks AI",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.fireworks.ai/inference/v1",
    connection_probe=bearer_models_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.fireworks import FireworksProvider
