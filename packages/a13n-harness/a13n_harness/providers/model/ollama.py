"""Ollama Provider adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Self

import httpx2
from pydantic import Field, StringConstraints, model_validator

from ..authentication import Authentication, CredentialMode
from .credentials import EmptyCredential
from .definition import (
    ConnectionProbeRequest,
    ModelProviderDefinition,
    join_url,
    require_endpoint,
)
from .types import ModelConnection, ProviderConfiguration


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
    provider: ModelConnection[Config, EmptyCredential],
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> OllamaProvider:
    from openai import AsyncOpenAI
    from pydantic_ai.providers.ollama import OllamaProvider

    return OllamaProvider(
        openai_client=AsyncOpenAI(
            api_key="ollama",
            base_url=require_endpoint(provider),
            http_client=http_client,
            max_retries=0,
            default_headers=provider.extra_headers,
        )
    )


def _request(provider: ModelConnection[Config, EmptyCredential]) -> ConnectionProbeRequest:
    endpoint = require_endpoint(provider).removesuffix("/v1")
    return ConnectionProbeRequest(url=join_url(endpoint, "api/tags"), headers={})


def _endpoint(configuration: Mapping[str, object]) -> str:
    return str(configuration["base_url"])


DEFINITION = ModelProviderDefinition(
    type="ollama",
    display_name="Ollama",
    configuration_model=Config,
    credential_model=EmptyCredential,
    supported_model_apis=("ollama.chat_completions",),
    build_provider=_build_provider,
    authentication=Authentication(mode=CredentialMode.forbidden),
    endpoint=_endpoint,
    connection_probe=_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.ollama import OllamaProvider
