"""Ollama Provider adapter."""

from collections.abc import Mapping
from typing import Annotated, Self

import httpx2
from openai import AsyncOpenAI
from pydantic import Field, StringConstraints, model_validator
from pydantic_ai.providers.ollama import OllamaProvider

from .base import (
    ConnectionProbeRequest,
    ProviderIntegration,
    join_url,
    require_endpoint,
)
from .types import ProviderConfiguration, RuntimeProvider


class Config(ProviderConfiguration):
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)] | None = Field(
        default=..., title="Base URL", description="The API endpoint of your Ollama server."
    )

    @model_validator(mode="after")
    def require_base_url(self) -> Self:
        if self.base_url is None:
            raise ValueError("Ollama requires a base_url")
        return self


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> OllamaProvider:
    return OllamaProvider(
        openai_client=AsyncOpenAI(
            api_key="ollama",
            base_url=require_endpoint(provider),
            http_client=http_client,
            max_retries=0,
            default_headers=provider.extra_headers,
        )
    )


def _request(provider: RuntimeProvider) -> ConnectionProbeRequest:
    endpoint = require_endpoint(provider).removesuffix("/v1")
    return ConnectionProbeRequest(url=join_url(endpoint, "api/tags"), headers={})


def _endpoint(configuration: Mapping[str, object]) -> str:
    return str(configuration["base_url"])


INTEGRATION = ProviderIntegration(
    type="ollama",
    display_name="Ollama",
    configuration_model=Config,
    supported_model_apis=("ollama.chat_completions",),
    build_provider=_build_provider,
    credential_format=None,
    credential_required=False,
    endpoint=_endpoint,
    endpoint_configuration_field="base_url",
    connection_probe=_request,
)
