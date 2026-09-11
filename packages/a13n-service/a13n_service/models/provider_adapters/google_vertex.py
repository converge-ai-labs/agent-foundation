"""Google Vertex AI Provider adapter."""

from typing import Annotated

import httpx2
from google.genai import Client
from pydantic import StringConstraints
from pydantic_ai.providers.google_cloud import GoogleCloudProvider

from ..credentials import parse_google_service_account
from .base import ProviderIntegration, require_credential
from .google_provider import http_options
from .types import CredentialFormat, ProviderConfiguration, RuntimeProvider


class Config(ProviderConfiguration):
    project_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    location: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> GoogleCloudProvider:
    return GoogleCloudProvider(
        client=Client(
            vertexai=True,
            credentials=parse_google_service_account(require_credential(provider)),
            project=str(provider.configuration["project_id"]),
            location=str(provider.configuration["location"]),
            http_options=http_options(provider, http_client),
        )
    )


INTEGRATION = ProviderIntegration(
    type="google_vertex",
    display_name="Google Vertex AI",
    configuration_model=Config,
    supported_model_apis=("google.generate_content",),
    build_provider=_build_provider,
    credential_format=CredentialFormat.google_service_account_json,
    reserved_headers=("authorization", "x-goog-api-key", "x-goog-user-project"),
)
