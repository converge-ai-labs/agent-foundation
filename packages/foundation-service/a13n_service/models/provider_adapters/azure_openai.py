"""Azure OpenAI Provider adapter."""

from collections.abc import Mapping
from typing import Annotated, Self, cast
from urllib.parse import urlsplit, urlunsplit

import httpx2
from pydantic import StringConstraints, model_validator
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.azure import AzureProvider

from ..domain import ModelExecutionSnapshot
from .base import (
    BuiltModel,
    ModelListRequest,
    ProviderAdapter,
    join_url,
    model_name,
    openai_style_discovery,
    require_credential,
    require_endpoint,
    unsupported_model_api,
)
from .types import ProviderConfig, ProviderType, RuntimeProvider


class Config(ProviderConfig):
    resource_endpoint: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    api_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def normalize_endpoint(self) -> Self:
        object.__setattr__(self, "resource_endpoint", _official_endpoint(self.resource_endpoint))
        return self


def _build(snapshot: ModelExecutionSnapshot, provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> BuiltModel:
    native_provider = AzureProvider(
        azure_endpoint=str(provider.config["resource_endpoint"]),
        api_version=cast(str | None, provider.config.get("api_version")),
        api_key=require_credential(provider),
        http_client=http_client,
    )
    if snapshot.model_api == "openai.responses":
        return OpenAIResponsesModel(model_name(snapshot), provider=native_provider)
    if snapshot.model_api == "openai.chat_completions":
        return OpenAIChatModel(model_name(snapshot), provider=native_provider)
    unsupported_model_api(snapshot)


def _request(provider: RuntimeProvider) -> ModelListRequest:
    return ModelListRequest(
        url=join_url(require_endpoint(provider), "models"),
        headers={"api-key": require_credential(provider)},
    )


ADAPTER = ProviderAdapter(
    build_model=_build,
    model_discovery=openai_style_discovery(_request),
)


def _endpoint(config: Mapping[str, object]) -> str:
    return str(config["resource_endpoint"])


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


TYPE = ProviderType(
    key="azure_openai",
    display_name="Azure OpenAI",
    config_model=Config,
    supported_model_apis=("openai.responses", "openai.chat_completions"),
    endpoint=_endpoint,
    endpoint_config_field="resource_endpoint",
    supports_model_discovery=True,
)
