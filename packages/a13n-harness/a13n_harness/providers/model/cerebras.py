"""Cerebras Provider adapter."""

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
) -> CerebrasProvider:
    from pydantic_ai.providers.cerebras import CerebrasProvider

    class _CerebrasProvider(openai_provider.ClientEndpointProvider, CerebrasProvider):
        pass

    return openai_provider.build(provider, http_client, model_api, _CerebrasProvider)


DEFINITION = ModelProviderDefinition(
    type="cerebras",
    catalog_providers=("cerebras",),
    setup_url="https://cloud.cerebras.ai/platform/api-keys",
    display_name="Cerebras",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.cerebras.ai/v1",
    connection_probe=bearer_models_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.cerebras import CerebrasProvider
