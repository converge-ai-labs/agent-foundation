"""Vercel AI Gateway Provider adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2

from . import openai_provider
from .credentials import ApiKeyCredential
from .definition import ModelProviderDefinition
from .types import ModelConnection, ProviderConfiguration


def _build_provider(
    provider: ModelConnection[ProviderConfiguration, ApiKeyCredential],
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> VercelProvider:
    from pydantic_ai.providers.vercel import VercelProvider

    class _VercelProvider(openai_provider.ClientEndpointProvider, VercelProvider):
        pass

    return openai_provider.build(provider, http_client, model_api, _VercelProvider)


DEFINITION = ModelProviderDefinition(
    type="vercel",
    catalog_providers=("vercel",),
    setup_url="https://vercel.com/dashboard/ai-gateway",
    display_name="Vercel AI Gateway",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://ai-gateway.vercel.sh/v1",
)

if TYPE_CHECKING:
    from pydantic_ai.providers.vercel import VercelProvider
