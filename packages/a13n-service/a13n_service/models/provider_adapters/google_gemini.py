"""Google Gemini Developer API Provider adapter."""

import httpx2
from google.genai import Client
from pydantic_ai.providers.google import GoogleProvider

from .base import (
    ConnectionProbeRequest,
    ProviderIntegration,
    join_url,
    require_credential,
    require_endpoint,
)
from .google_provider import http_options
from .types import ProviderConfiguration, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> GoogleProvider:
    return GoogleProvider(
        client=Client(
            vertexai=False, api_key=require_credential(provider), http_options=http_options(provider, http_client)
        )
    )


def _request(provider: RuntimeProvider) -> ConnectionProbeRequest:
    return ConnectionProbeRequest(
        url=join_url(require_endpoint(provider), "v1beta/models"),
        headers={"x-goog-api-key": require_credential(provider)},
    )


INTEGRATION = ProviderIntegration(
    type="google_gemini",
    display_name="Google Gemini",
    configuration_model=ProviderConfiguration,
    supported_model_apis=("google.generate_content",),
    build_provider=_build_provider,
    endpoint="https://generativelanguage.googleapis.com",
    reserved_headers=("x-goog-api-key",),
    connection_probe=_request,
)
