"""Build Pydantic AI models for trusted Provider and calling-API combinations."""

from __future__ import annotations

from typing import Any, cast

import httpx2
from a13n_harness.errors import ModelResolutionError
from openai import AsyncOpenAI
from pydantic_ai.models import Model as PydanticModel
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.bedrock_mantle import BedrockMantleChatModel, BedrockMantleResponsesModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.alibaba import AlibabaProvider
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.azure import AzureProvider
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.providers.bedrock_mantle import BedrockMantleProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.google_cloud import GoogleCloudProvider
from pydantic_ai.providers.moonshotai import MoonshotAIProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.providers.zai import ZaiProvider

from .credentials import parse_aws_credentials, parse_google_service_account
from .domain import ModelExecutionSnapshot
from .provider_runtime import RuntimeProvider


class NativeModelFactory:
    """Trusted mapping from one calling API to its Pydantic AI Model implementation."""

    def __init__(self, http_client: httpx2.AsyncClient) -> None:
        self._http_client = http_client

    def build(self, snapshot: ModelExecutionSnapshot, provider: RuntimeProvider) -> PydanticModel[Any]:
        model_name = cast(Any, snapshot.upstream_model)
        api = snapshot.model_api
        api_key = provider.credential
        if api == "openai.responses":
            return OpenAIResponsesModel(model_name, provider=self._openai_provider(provider))
        if api == "openai.chat_completions":
            return OpenAIChatModel(model_name, provider=self._openai_chat_provider(provider))
        if api == "anthropic.messages":
            return AnthropicModel(
                model_name,
                provider=AnthropicProvider(
                    api_key=_require_credential(api_key, provider.type),
                    base_url=provider.endpoint,
                    http_client=self._http_client,
                ),
            )
        if api == "google.generate_content":
            return self._google_model(model_name, provider)
        if api == "bedrock.converse":
            credentials = parse_aws_credentials(_require_credential(api_key, provider.type))
            return BedrockConverseModel(
                model_name,
                provider=BedrockProvider(region_name=str(provider.config["region"]), **credentials.model_dump()),
            )
        if api in {"bedrock_mantle.responses", "bedrock_mantle.chat_completions"}:
            credentials = parse_aws_credentials(_require_credential(api_key, provider.type))
            mantle = BedrockMantleProvider(region_name=str(provider.config["region"]), **credentials.model_dump())
            if api == "bedrock_mantle.responses":
                return BedrockMantleResponsesModel(model_name, provider=mantle)
            return BedrockMantleChatModel(model_name, provider=mantle)
        if api == "openrouter.chat_completions":
            return OpenRouterModel(
                model_name,
                provider=OpenRouterProvider(
                    api_key=_require_credential(api_key, provider.type),
                    http_client=self._http_client,
                ),
            )
        if api == "ollama.chat_completions":
            return OllamaModel(
                model_name,
                provider=OllamaProvider(base_url=str(provider.config["base_url"]), http_client=self._http_client),
            )
        raise ModelResolutionError(
            "The accepted Model API is unavailable.",
            code="model_api_unavailable",
            details={"model_api": api},
        )

    def _openai_provider(self, provider: RuntimeProvider) -> OpenAIProvider | AzureProvider:
        if provider.type == "openai_compatible":
            return _compatible_openai_provider(provider, self._http_client)
        api_key = _require_credential(provider.credential, provider.type)
        if provider.type == "azure_openai":
            return AzureProvider(
                azure_endpoint=str(provider.config["resource_endpoint"]),
                api_version=cast(str | None, provider.config.get("api_version")),
                api_key=api_key,
                http_client=self._http_client,
            )
        return OpenAIProvider(api_key=api_key, base_url=provider.endpoint, http_client=self._http_client)

    def _openai_chat_provider(self, provider: RuntimeProvider):
        if provider.type in {"openai", "azure_openai", "openai_compatible"}:
            return self._openai_provider(provider)
        key = _require_credential(provider.credential, provider.type)
        client = _openai_client(api_key=key, base_url=provider.endpoint, http_client=self._http_client)
        provider_classes = {
            "alibaba_model_studio": AlibabaProvider,
            "deepseek": DeepSeekProvider,
            "moonshot": MoonshotAIProvider,
            "zhipu": ZaiProvider,
        }
        try:
            return provider_classes[provider.type](openai_client=client)
        except KeyError as error:
            raise ModelResolutionError(
                "The Model Provider is unavailable.", code="model_provider_unavailable"
            ) from error

    def _google_model(self, model_name: Any, provider: RuntimeProvider) -> PydanticModel[Any]:
        credential = _require_credential(provider.credential, provider.type)
        if provider.type == "google_vertex":
            return GoogleModel(
                model_name,
                provider=GoogleCloudProvider(
                    credentials=parse_google_service_account(credential),
                    project=str(provider.config["project_id"]),
                    location=str(provider.config["location"]),
                    http_client=self._http_client,
                ),
            )
        return GoogleModel(
            model_name,
            provider=GoogleProvider(api_key=credential, base_url=provider.endpoint, http_client=self._http_client),
        )


def _require_credential(value: str | None, provider_type: str) -> str:
    if not value:
        raise ModelResolutionError(
            "The Model Provider credential is unavailable.",
            code="model_provider_credential_unavailable",
            details={"provider_type": provider_type},
        )
    return value


def _openai_client(*, api_key: str, base_url: str | None, http_client: httpx2.AsyncClient) -> AsyncOpenAI:
    if base_url is None:
        raise ValueError("the Model Provider endpoint is missing")
    return AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)


def _compatible_openai_provider(provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> OpenAIProvider:
    config = provider.config
    credential = provider.credential or ""
    default_headers = None
    if config["auth_mode"] == "api_key_header":
        default_headers = {str(config["api_key_header_name"]): credential}
    client = AsyncOpenAI(
        api_key=credential,
        base_url=str(config["base_url"]),
        default_headers=default_headers,
        http_client=http_client,
        _enforce_credentials=False,
    )
    return OpenAIProvider(openai_client=client)
