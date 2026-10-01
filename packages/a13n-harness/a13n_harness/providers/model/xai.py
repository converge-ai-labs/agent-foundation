"""xAI's HTTP Chat Completions connection; the native xai: route remains gRPC."""

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
    from pydantic_ai.profiles import ModelProfile, merge_profile
    from pydantic_ai.profiles.grok import grok_model_profile
    from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer, OpenAIModelProfile
    from pydantic_ai.providers.openai import OpenAIProvider

    class XaiChatProvider(OpenAIProvider):
        @property
        def name(self) -> str:
            return "xai"

        @staticmethod
        def model_profile(model_name: str) -> ModelProfile | None:
            return merge_profile(
                OpenAIModelProfile(json_schema_transformer=OpenAIJsonSchemaTransformer),
                grok_model_profile(model_name),
                # xAI's native server-side tools require its other API dialects.
                ModelProfile(supported_native_tools=frozenset()),
            )

    return openai_provider.build(provider, http_client, model_api, XaiChatProvider)


DEFINITION = ModelProviderDefinition(
    type="xai",
    catalog_providers=("xai", "x-ai"),
    setup_url="https://console.x.ai/",
    display_name="xAI / Grok",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.x.ai/v1",
    connection_probe=bearer_models_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.openai import OpenAIProvider
