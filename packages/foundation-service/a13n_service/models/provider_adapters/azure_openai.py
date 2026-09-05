"""Azure OpenAI Provider adapter."""

from collections.abc import Mapping
from typing import Annotated, Self, cast
from urllib.parse import urlsplit, urlunsplit

import httpx2
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
    resource_endpoint: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    api_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def normalize_endpoint(self) -> Self:
        object.__setattr__(self, "resource_endpoint", _official_endpoint(self.resource_endpoint))
        return self


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _pydantic_provider_name: str,
) -> AzureProvider:
    return AzureProvider(
        azure_endpoint=str(provider.configuration["resource_endpoint"]),
        api_version=cast(str | None, provider.configuration.get("api_version")),
        api_key=require_credential(provider),
        http_client=http_client,
    )


def _request(provider: RuntimeProvider) -> ModelListRequest:
    return ModelListRequest(
        url=join_url(require_endpoint(provider), "models"),
        headers={"api-key": require_credential(provider)},
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
        if path:
            raise ValueError("Azure AI model resource_endpoint must not contain a path")
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
    model_discovery=openai_style_discovery(_request),
)
