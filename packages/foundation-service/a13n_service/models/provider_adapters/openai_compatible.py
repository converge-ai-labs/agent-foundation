"""User-defined OpenAI-compatible Provider adapter."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Self

import httpx2
from openai import AsyncOpenAI
from pydantic import StringConstraints, model_validator
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..domain import ModelExecutionSnapshot
from .base import (
    BuiltModel,
    ModelListRequest,
    ProviderAdapter,
    join_url,
    model_name,
    openai_style_discovery,
    require_endpoint,
    unsupported_model_api,
)
from .types import ProviderConfig, ProviderType, RuntimeProvider

_RESERVED_HEADERS = {
    "connection",
    "content-length",
    "cookie",
    "host",
    "proxy-authorization",
    "set-cookie",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class AuthMode(StrEnum):
    none = "none"
    bearer = "bearer"
    api_key_header = "api_key_header"


class Config(ProviderConfig):
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    auth_mode: AuthMode = AuthMode.bearer
    api_key_header_name: Annotated[str, StringConstraints(pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")] | None = None

    @model_validator(mode="after")
    def validate_header_mode(self) -> Self:
        if self.auth_mode is AuthMode.api_key_header and self.api_key_header_name is None:
            raise ValueError("api_key_header_name is required for api_key_header auth")
        if self.auth_mode is not AuthMode.api_key_header and self.api_key_header_name is not None:
            raise ValueError("api_key_header_name is accepted only for api_key_header auth")
        if self.api_key_header_name is not None and self.api_key_header_name.lower() in _RESERVED_HEADERS:
            raise ValueError("api_key_header_name is reserved by HTTP")
        return self


def _build(snapshot: ModelExecutionSnapshot, provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> BuiltModel:
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
    native_provider = OpenAIProvider(openai_client=client)
    if snapshot.model_api == "openai.responses":
        return OpenAIResponsesModel(model_name(snapshot), provider=native_provider)
    if snapshot.model_api == "openai.chat_completions":
        return OpenAIChatModel(model_name(snapshot), provider=native_provider)
    unsupported_model_api(snapshot)


def _request(provider: RuntimeProvider) -> ModelListRequest:
    headers: dict[str, str] = {}
    if provider.credential:
        if provider.config.get("auth_mode") == "api_key_header":
            headers[str(provider.config["api_key_header_name"])] = provider.credential
        else:
            headers["authorization"] = f"Bearer {provider.credential}"
    return ModelListRequest(url=join_url(require_endpoint(provider), "models"), headers=headers)


ADAPTER = ProviderAdapter(
    build_model=_build,
    model_discovery=openai_style_discovery(_request),
)


def _endpoint(config: Mapping[str, object]) -> str:
    return str(config["base_url"])


def _validate_credential(config: Mapping[str, object], configured: bool) -> None:
    unauthenticated = config["auth_mode"] == AuthMode.none.value
    if unauthenticated and configured:
        raise ValueError("the unauthenticated mode does not accept a credential")
    if not unauthenticated and not configured:
        raise ValueError("the configured authentication mode requires a credential")


TYPE = ProviderType(
    key="openai_compatible",
    display_name="OpenAI-Compatible",
    config_model=Config,
    supported_model_apis=("openai.responses", "openai.chat_completions"),
    credential_required=False,
    endpoint=_endpoint,
    endpoint_config_field="base_url",
    credential_validator=_validate_credential,
    supports_model_discovery=True,
)
