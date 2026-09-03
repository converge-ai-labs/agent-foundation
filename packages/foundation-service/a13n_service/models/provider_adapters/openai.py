"""OpenAI Provider adapter."""

import httpx2
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..domain import ModelExecutionSnapshot
from .base import (
    BuiltModel,
    ProviderAdapter,
    bearer_models_request,
    model_name,
    openai_style_discovery,
    require_credential,
    unsupported_model_api,
)
from .types import EmptyProviderConfig, ProviderType, RuntimeProvider


def _build(snapshot: ModelExecutionSnapshot, provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> BuiltModel:
    native_provider = OpenAIProvider(
        api_key=require_credential(provider),
        base_url=provider.endpoint,
        http_client=http_client,
    )
    if snapshot.model_api == "openai.responses":
        return OpenAIResponsesModel(model_name(snapshot), provider=native_provider)
    if snapshot.model_api == "openai.chat_completions":
        return OpenAIChatModel(model_name(snapshot), provider=native_provider)
    unsupported_model_api(snapshot)


ADAPTER = ProviderAdapter(
    build_model=_build,
    model_discovery=openai_style_discovery(bearer_models_request),
)

TYPE = ProviderType(
    key="openai",
    display_name="OpenAI",
    config_model=EmptyProviderConfig,
    supported_model_apis=("openai.responses", "openai.chat_completions"),
    endpoint="https://api.openai.com/v1",
    supports_model_discovery=True,
)
