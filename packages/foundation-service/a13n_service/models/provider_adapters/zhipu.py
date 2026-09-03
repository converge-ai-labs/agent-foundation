"""Zhipu Provider adapter."""

import httpx2
from pydantic_ai.providers.zai import ZaiProvider

from ..domain import ModelExecutionSnapshot
from . import openai_chat
from .base import BuiltModel, ProviderAdapter, bearer_models_request, openai_style_discovery
from .types import EmptyProviderConfig, ProviderType, RuntimeProvider


def _build(snapshot: ModelExecutionSnapshot, provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> BuiltModel:
    return openai_chat.build(snapshot, provider, http_client, ZaiProvider)


ADAPTER = ProviderAdapter(
    build_model=_build,
    model_discovery=openai_style_discovery(bearer_models_request),
)

TYPE = ProviderType(
    key="zhipu",
    display_name="Zhipu / GLM",
    config_model=EmptyProviderConfig,
    supported_model_apis=("openai.chat_completions",),
    endpoint="https://open.bigmodel.cn/api/paas/v4",
    supports_model_discovery=True,
)
