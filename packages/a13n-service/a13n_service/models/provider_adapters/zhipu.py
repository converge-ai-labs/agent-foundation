"""Zhipu Provider adapter."""

import httpx2
from pydantic_ai.providers.zai import ZaiProvider

from . import openai_provider
from .base import ProviderIntegration, bearer_models_request
from .types import ProviderConfiguration, RuntimeProvider


class _ZaiProvider(openai_provider.ClientEndpointProvider, ZaiProvider):
    pass


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> ZaiProvider:
    return openai_provider.build(provider, http_client, model_api, _ZaiProvider)


INTEGRATION = ProviderIntegration(
    type="zhipu",
    display_name="Zhipu / GLM",
    configuration_model=ProviderConfiguration,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://open.bigmodel.cn/api/paas/v4",
    connection_probe=bearer_models_request,
)
