"""OpenAI protocols with official defaults and configurable connection authentication."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Self, override

import httpx2
from openai import AsyncOpenAI, Omit
from pydantic import model_validator
from pydantic_ai.providers.openai import OpenAIProvider

from ..headers import HeaderName
from .base import (
    ModelListRequest,
    ProviderIntegration,
    join_url,
    require_endpoint,
)
from .openai_provider import openai_style_discovery
from .types import ProviderConfiguration, RuntimeProvider


class AuthMode(StrEnum):
    none = "none"
    bearer = "bearer"
    api_key_header = "api_key_header"


class Config(ProviderConfiguration):
    auth_mode: AuthMode = AuthMode.bearer
    api_key_header_name: HeaderName | None = None

    @model_validator(mode="after")
    def validate_header_mode(self) -> Self:
        if self.auth_mode is AuthMode.api_key_header and self.api_key_header_name is None:
            raise ValueError("api_key_header_name is required for api_key_header auth")
        if self.auth_mode is not AuthMode.api_key_header and self.api_key_header_name is not None:
            raise ValueError("api_key_header_name is accepted only for api_key_header auth")
        return self

    @property
    def authentication_headers(self) -> tuple[str, ...]:
        return (self.api_key_header_name,) if self.api_key_header_name is not None else ()


class _ConfiguredOpenAI(AsyncOpenAI):
    @override
    def _validate_headers(self, headers: Mapping[str, str | Omit], custom_headers: Mapping[str, str | Omit]) -> None:
        # This adapter validates its selected authentication mode before construction.
        # OpenAI's bearer-only request check rejects no-auth and custom-header servers.
        return None


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> OpenAIProvider:
    configuration = provider.configuration
    credential = provider.credential or ""
    default_headers = dict(provider.extra_headers)
    if configuration.get("auth_mode", "bearer") == "api_key_header":
        header_name = str(configuration["api_key_header_name"])
        default_headers["Authorization" if header_name.lower() == "authorization" else header_name] = credential
    client = _ConfiguredOpenAI(
        max_retries=0,
        api_key=credential if configuration.get("auth_mode", "bearer") == "bearer" else "",
        admin_api_key="",
        base_url=require_endpoint(provider),
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


def _validate_credential(configuration: Mapping[str, object], configured: bool) -> None:
    unauthenticated = configuration.get("auth_mode", "bearer") == AuthMode.none.value
    if unauthenticated and configured:
        raise ValueError("the unauthenticated mode does not accept a credential")
    if not unauthenticated and not configured:
        raise ValueError("the configured authentication mode requires a credential")


INTEGRATION = ProviderIntegration(
    type="openai",
    display_name="OpenAI",
    configuration_model=Config,
    supported_model_apis=("openai.responses", "openai.chat_completions"),
    build_provider=_build_provider,
    credential_required=False,
    endpoint="https://api.openai.com/v1",
    credential_validator=_validate_credential,
    model_discovery=openai_style_discovery(_request),
    model_profile=OpenAIProvider.model_profile,
)
