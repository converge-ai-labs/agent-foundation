"""Azure OpenAI Provider adapter."""

from collections.abc import Mapping
from typing import Annotated, Self, cast
from urllib.parse import urlsplit, urlunsplit

import httpx2
from openai import AsyncAzureOpenAI, AsyncOpenAI
from pydantic import StringConstraints, model_validator
from pydantic_ai.providers.azure import AzureProvider

from .base import (
    ModelListRequest,
    ProviderIntegration,
    join_url,
    require_credential,
    require_endpoint,
)
from .openai_provider import openai_style_discovery
from .types import ProviderConfiguration, RuntimeProvider


class Config(ProviderConfiguration):
    resource_endpoint: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)] | None
    ) = None
    api_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def normalize_endpoint(self) -> Self:
        if self.base_url is None and self.resource_endpoint is None:
            raise ValueError("resource_endpoint or base_url is required")
        if self.resource_endpoint is not None:
            object.__setattr__(self, "resource_endpoint", _official_endpoint(self.resource_endpoint))
        if self.api_version is not None and (self.base_url or self.resource_endpoint or "").rstrip("/").endswith("/v1"):
            raise ValueError("api_version must be omitted for the Azure v1 API")
        return self


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> AzureProvider:
    endpoint = require_endpoint(provider)
    version = provider.configuration.get("api_version")
    if version is not None:
        client = AsyncAzureOpenAI(
            base_url=endpoint,
            api_version=str(version),
            api_key=require_credential(provider),
            http_client=http_client,
            max_retries=0,
            default_headers=provider.extra_headers,
        )
    else:
        client = AsyncOpenAI(
            base_url=endpoint,
            api_key=require_credential(provider),
            http_client=http_client,
            max_retries=0,
            default_headers=provider.extra_headers,
        )
    # AzureProvider itself uses AsyncOpenAI for the v1 API, although its injection annotation is narrower.
    return AzureProvider(openai_client=cast(AsyncAzureOpenAI, client))


def _request(provider: RuntimeProvider) -> ModelListRequest:
    return ModelListRequest(
        url=join_url(require_endpoint(provider), "models"),
        headers=(
            {"api-key": require_credential(provider)}
            if provider.configuration.get("api_version")
            else {"authorization": f"Bearer {require_credential(provider)}"}
        ),
    )


def _endpoint(configuration: Mapping[str, object]) -> str:
    return str(configuration["resource_endpoint"])


def _official_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("resource_endpoint must use the official Azure HTTPS endpoint")
    if hostname.endswith(".openai.azure.com"):
        path = parsed.path.rstrip("/")
        if path not in {"", "/openai/v1"}:
            raise ValueError("Azure OpenAI resource_endpoint must select the v1 API")
        path = "/openai/v1"
    elif hostname.endswith(".models.ai.azure.com"):
        path = parsed.path.rstrip("/")
        if path not in {"", "/v1"}:
            raise ValueError("Azure AI model resource_endpoint must select the v1 API")
        path = "/v1"
    else:
        raise ValueError("resource_endpoint must use an official Azure model domain")
    return urlunsplit(("https", parsed.netloc, path, "", ""))


INTEGRATION = ProviderIntegration(
    type="azure_openai",
    display_name="Azure OpenAI",
    configuration_model=Config,
    supported_model_apis=("openai.responses", "openai.chat_completions"),
    build_provider=_build_provider,
    endpoint=_endpoint,
    endpoint_configuration_field="resource_endpoint",
    reserved_headers=("authorization", "api-key"),
    model_discovery=openai_style_discovery(_request),
)
