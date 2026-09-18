"""MiniMax's OpenAI-compatible Chat Completions connection."""

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
) -> OpenAIProvider:
    from pydantic_ai.providers.openai import OpenAIProvider

    return openai_provider.build(provider, http_client, model_api, OpenAIProvider)


DEFINITION = ModelProviderDefinition(
    type="minimax",
    setup_url="https://platform.minimax.io/docs/guides/quickstart-preparation",
    display_name="MiniMax",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.minimax.io/v1",
    connection_probe=bearer_models_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.openai import OpenAIProvider
