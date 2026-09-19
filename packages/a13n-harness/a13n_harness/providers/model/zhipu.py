"""Zhipu Provider adapter."""

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
) -> ZaiProvider:
    from pydantic_ai.providers.zai import ZaiProvider

    class _ZaiProvider(openai_provider.ClientEndpointProvider, ZaiProvider):
        pass

    return openai_provider.build(provider, http_client, model_api, _ZaiProvider)


DEFINITION = ModelProviderDefinition(
    type="zhipu",
    catalog_providers=("zhipuai", "zai"),
    setup_url="https://docs.bigmodel.cn/cn/guide/develop/apikey",
    display_name="Zhipu / GLM",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://open.bigmodel.cn/api/paas/v4",
    connection_probe=bearer_models_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.zai import ZaiProvider
