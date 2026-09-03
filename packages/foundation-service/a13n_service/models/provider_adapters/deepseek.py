"""DeepSeek Provider adapter."""

import httpx2
from pydantic_ai.providers.deepseek import DeepSeekProvider

from ..domain import ModelExecutionSnapshot
from . import openai_chat
from .base import BuiltModel, ProviderAdapter, bearer_models_request, openai_style_discovery
from .types import EmptyProviderConfig, ProviderType, RuntimeProvider


def _build(snapshot: ModelExecutionSnapshot, provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> BuiltModel:
    return openai_chat.build(snapshot, provider, http_client, DeepSeekProvider)


ADAPTER = ProviderAdapter(
    build_model=_build,
    model_discovery=openai_style_discovery(bearer_models_request),
)

TYPE = ProviderType(
    key="deepseek",
    display_name="DeepSeek",
    config_model=EmptyProviderConfig,
    supported_model_apis=("openai.chat_completions",),
    endpoint="https://api.deepseek.com",
    supports_model_discovery=True,
)
