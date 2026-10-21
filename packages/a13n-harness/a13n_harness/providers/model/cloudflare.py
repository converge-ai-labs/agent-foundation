"""Cloudflare AI Gateway's account-scoped unified REST API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated

import httpx2
from pydantic import Field, StringConstraints, model_validator

from .credentials import ApiKeyCredential
from .definition import ModelProviderDefinition, require_credential, require_endpoint
from .types import ModelConnection, ProviderConfiguration


class CloudflareConfiguration(ProviderConfiguration):
    account_id: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{32}$")] | None = Field(
        default=None, description="Cloudflare account ID. Required unless Base URL is supplied."
    )
    gateway_id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=64)] | None = Field(
        default=None, description="Optional AI Gateway ID; omitted third-party requests use the default gateway."
    )

    @model_validator(mode="after")
    def require_account_endpoint(self) -> CloudflareConfiguration:
        if self.account_id is None and self.base_url is None:
            raise ValueError("Cloudflare requires account_id or base_url ending in /ai")
        return self


def _endpoint(configuration: Mapping[str, object]) -> str:
    return f"https://api.cloudflare.com/client/v4/accounts/{configuration['account_id']}/ai"


def _build_provider(
    connection: ModelConnection[CloudflareConfiguration, ApiKeyCredential],
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> Provider:
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI
    from pydantic_ai.profiles import ModelProfile, merge_profile
    from pydantic_ai.profiles.anthropic import anthropic_model_profile
    from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer, OpenAIModelProfile, openai_model_profile
    from pydantic_ai.providers.anthropic import AnthropicProvider
    from pydantic_ai.providers.openai import OpenAIProvider

    class CloudflareOpenAIProvider(OpenAIProvider):
        @property
        def name(self) -> str:
            return "cloudflare_ai_gateway"

        @staticmethod
        def model_profile(model_name: str) -> ModelProfile:
            publisher, _, upstream = model_name.partition("/")
            return merge_profile(
                OpenAIModelProfile(json_schema_transformer=OpenAIJsonSchemaTransformer),
                openai_model_profile(upstream) if publisher == "openai" else None,
                ModelProfile(supported_native_tools=frozenset()),
            )

    class CloudflareAnthropicProvider(AnthropicProvider):
        @property
        def name(self) -> str:
            return "cloudflare_ai_gateway"

        @staticmethod
        def model_profile(model_name: str) -> ModelProfile:
            return merge_profile(
                anthropic_model_profile(model_name.removeprefix("anthropic/").replace(".", "-")),
                ModelProfile(supported_native_tools=frozenset()),
            )

    headers = dict(connection.extra_headers)
    if connection.configuration.gateway_id is not None:
        headers["cf-aig-gateway-id"] = connection.configuration.gateway_id
    endpoint = require_endpoint(connection).rstrip("/")
    token = require_credential(connection)
    if model_api == "anthropic.messages":
        return CloudflareAnthropicProvider(
            anthropic_client=AsyncAnthropic(
                api_key=None,
                auth_token=token,
                base_url=endpoint,
                http_client=http_client,
                max_retries=0,
                default_headers=headers,
            )
        )
    return CloudflareOpenAIProvider(
        openai_client=AsyncOpenAI(
            api_key=token,
            base_url=f"{endpoint}/v1",
            http_client=http_client,
            max_retries=0,
            default_headers=headers,
        )
    )


DEFINITION = ModelProviderDefinition(
    type="cloudflare_ai_gateway",
    display_name="Cloudflare AI Gateway",
    setup_url="https://dash.cloudflare.com/",
    catalog_providers=("cloudflare-ai-gateway",),
    configuration_model=CloudflareConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openai.chat_completions", "openai.responses", "anthropic.messages"),
    build_provider=_build_provider,
    endpoint=_endpoint,
    reserved_headers=("authorization", "x-api-key", "cf-aig-gateway-id", "anthropic-version", "anthropic-beta"),
)

if TYPE_CHECKING:
    from pydantic_ai.providers import Provider
