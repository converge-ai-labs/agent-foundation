"""OpenAI protocols with official defaults and configurable connection authentication."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Self, override

import httpx2
from pydantic import Field, model_validator

from ..authentication import Authentication, AuthenticationCase, CredentialMode
from .credentials import ApiKeyCredential
from .definition import (
    ConnectionProbeRequest,
    ModelProviderDefinition,
    join_url,
    require_endpoint,
)
from .headers import HeaderName
from .types import ModelConnection, ProviderConfiguration


class AuthMode(StrEnum):
    none = "none"
    bearer = "bearer"
    api_key_header = "api_key_header"


class Config(ProviderConfiguration):
    auth_mode: AuthMode = Field(
        default=AuthMode.bearer,
        title="Authentication",
        json_schema_extra={
            "oneOf": [
                {"const": "bearer", "title": "Bearer token"},
                {"const": "none", "title": "None"},
                {"const": "api_key_header", "title": "Custom header"},
            ]
        },
    )
    api_key_header_name: HeaderName | None = Field(
        default=None, title="Header name", json_schema_extra={"x-visible-when": {"auth_mode": "api_key_header"}}
    )

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


def _build_provider(
    provider: ModelConnection[Config, ApiKeyCredential],
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> OpenAIProvider:
    from openai import AsyncOpenAI
    from pydantic_ai.providers.openai import OpenAIProvider

    class _ConfiguredOpenAI(AsyncOpenAI):
        @override
        def _validate_headers(
            self, headers: Mapping[str, str | Omit], custom_headers: Mapping[str, str | Omit]
        ) -> None:
            # This adapter validates its selected authentication mode before construction.
            # OpenAI's bearer-only request check rejects no-auth and custom-header servers.
            return None

    configuration = provider.configuration
    credential = provider.credential.api_key.get_secret_value() if provider.credential is not None else ""
    default_headers = dict(provider.extra_headers)
    if configuration.auth_mode == "api_key_header":
        header_name = str(configuration.api_key_header_name)
        default_headers["Authorization" if header_name.lower() == "authorization" else header_name] = credential
    client = _ConfiguredOpenAI(
        max_retries=0,
        api_key=credential if configuration.auth_mode == "bearer" else "",
        admin_api_key="",
        base_url=require_endpoint(provider),
        default_headers=default_headers,
        http_client=http_client,
        _enforce_credentials=False,
    )
    return OpenAIProvider(openai_client=client)


def _request(provider: ModelConnection[Config, ApiKeyCredential]) -> ConnectionProbeRequest:
    headers: dict[str, str] = {}
    if provider.credential is not None:
        if provider.configuration.auth_mode == "api_key_header":
            headers[str(provider.configuration.api_key_header_name)] = provider.credential.api_key.get_secret_value()
        else:
            headers["authorization"] = f"Bearer {provider.credential.api_key.get_secret_value()}"
    return ConnectionProbeRequest(url=join_url(require_endpoint(provider), "models"), headers=headers)


DEFINITION = ModelProviderDefinition(
    type="openai",
    catalog_providers=("openai",),
    setup_url="https://platform.openai.com/api-keys",
    display_name="OpenAI",
    configuration_model=Config,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openai.responses", "openai.chat_completions"),
    build_provider=_build_provider,
    authentication=Authentication(
        cases=(AuthenticationCase(field="auth_mode", equals="none", mode=CredentialMode.forbidden),)
    ),
    endpoint="https://api.openai.com/v1",
    connection_probe=_request,
)

if TYPE_CHECKING:
    from openai import Omit
    from pydantic_ai.providers.openai import OpenAIProvider
