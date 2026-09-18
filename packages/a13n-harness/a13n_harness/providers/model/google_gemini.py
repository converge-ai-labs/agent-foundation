"""Google Gemini Developer API Provider adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2

from .credentials import ApiKeyCredential
from .definition import (
    ConnectionProbeRequest,
    ModelProviderDefinition,
    join_url,
    require_credential,
    require_endpoint,
)
from .types import ModelConnection, ProviderConfiguration


def _build_provider(
    provider: ModelConnection[ProviderConfiguration, ApiKeyCredential],
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> GoogleProvider:
    from google.genai import Client
    from pydantic_ai.providers.google import GoogleProvider

    from .google_provider import http_options

    return GoogleProvider(
        client=Client(
            vertexai=False, api_key=require_credential(provider), http_options=http_options(provider, http_client)
        )
    )


def _request(provider: ModelConnection[ProviderConfiguration, ApiKeyCredential]) -> ConnectionProbeRequest:
    return ConnectionProbeRequest(
        url=join_url(require_endpoint(provider), "v1beta/models"),
        headers={"x-goog-api-key": require_credential(provider)},
    )


DEFINITION = ModelProviderDefinition(
    type="google_gemini",
    setup_url="https://aistudio.google.com/app/apikey",
    display_name="Google Gemini",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("google.generate_content",),
    build_provider=_build_provider,
    endpoint="https://generativelanguage.googleapis.com",
    reserved_headers=("x-goog-api-key",),
    connection_probe=_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.google import GoogleProvider
