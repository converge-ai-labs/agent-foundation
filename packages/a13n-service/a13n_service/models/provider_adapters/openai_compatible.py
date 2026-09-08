"""User-defined OpenAI-compatible Provider adapter."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Self

import httpx2
from openai import AsyncOpenAI
from pydantic import StringConstraints, model_validator
from pydantic_ai.providers.openai import OpenAIProvider

from .base import (
    ModelListRequest,
    ProviderIntegration,
    join_url,
    require_endpoint,
)
from .openai_provider import openai_style_discovery
from .types import ProviderConfiguration, RuntimeProvider

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


class Config(ProviderConfiguration):
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


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> OpenAIProvider:
    configuration = provider.configuration
    credential = provider.credential or ""
    default_headers = None
    if configuration["auth_mode"] == "api_key_header":
        default_headers = {str(configuration["api_key_header_name"]): credential}
    client = AsyncOpenAI(
        max_retries=0,
        api_key=credential,
        base_url=str(configuration["base_url"]),
        default_headers=default_headers,
        http_client=http_client,
        _enforce_credentials=False,
    )
    return OpenAIProvider(openai_client=client)


def _request(provider: RuntimeProvider) -> ModelListRequest:
    headers: dict[str, str] = {}
    if provider.credential:
        if provider.configuration.get("auth_mode") == "api_key_header":
            headers[str(provider.configuration["api_key_header_name"])] = provider.credential
        else:
            headers["authorization"] = f"Bearer {provider.credential}"
    return ModelListRequest(url=join_url(require_endpoint(provider), "models"), headers=headers)


def _endpoint(configuration: Mapping[str, object]) -> str:
    return str(configuration["base_url"])


def _validate_credential(configuration: Mapping[str, object], configured: bool) -> None:
    unauthenticated = configuration["auth_mode"] == AuthMode.none.value
    if unauthenticated and configured:
        raise ValueError("the unauthenticated mode does not accept a credential")
    if not unauthenticated and not configured:
        raise ValueError("the configured authentication mode requires a credential")


INTEGRATION = ProviderIntegration(
    type="openai_compatible",
    display_name="OpenAI-Compatible",
    configuration_model=Config,
    supported_model_apis=("openai.chat_completions", "openai.responses"),
    build_provider=_build_provider,
    credential_required=False,
    endpoint=_endpoint,
    endpoint_configuration_field="base_url",
    credential_validator=_validate_credential,
    model_discovery=openai_style_discovery(_request),
)
