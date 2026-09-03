"""OpenRouter Provider adapter."""

import httpx2
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from ..domain import ModelExecutionSnapshot
from .base import (
    BuiltModel,
    ProviderAdapter,
    bearer_models_request,
    model_name,
    openai_style_discovery,
    require_api,
    require_credential,
)
from .types import EmptyProviderConfig, ProviderType, RuntimeProvider


def _build(snapshot: ModelExecutionSnapshot, provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> BuiltModel:
    require_api(snapshot, "openrouter.chat_completions")
    return OpenRouterModel(
        model_name(snapshot),
        provider=OpenRouterProvider(api_key=require_credential(provider), http_client=http_client),
    )


ADAPTER = ProviderAdapter(
    build_model=_build,
    model_discovery=openai_style_discovery(bearer_models_request),
)

TYPE = ProviderType(
    key="openrouter",
    display_name="OpenRouter",
    config_model=EmptyProviderConfig,
    supported_model_apis=("openrouter.chat_completions",),
    endpoint="https://openrouter.ai/api/v1",
    supports_model_discovery=True,
)
