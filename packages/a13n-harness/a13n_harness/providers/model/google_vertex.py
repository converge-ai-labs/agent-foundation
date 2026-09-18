"""Google Vertex AI Provider adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import httpx2
from pydantic import StringConstraints

from .credentials import GoogleServiceAccount
from .definition import ModelProviderDefinition
from .types import ModelConnection, ProviderConfiguration


class Config(ProviderConfiguration):
    project_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    location: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]


def _build_provider(
    provider: ModelConnection[Config, GoogleServiceAccount],
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> GoogleCloudProvider:
    from google.genai import Client
    from pydantic_ai.providers.google_cloud import GoogleCloudProvider

    from .google_provider import http_options

    assert provider.credential is not None
    return GoogleCloudProvider(
        client=Client(
            vertexai=True,
            credentials=provider.credential.native_credentials(),
            project=str(provider.configuration.project_id),
            location=str(provider.configuration.location),
            http_options=http_options(provider, http_client),
        )
    )


DEFINITION = ModelProviderDefinition(
    type="google_vertex",
    setup_url="https://cloud.google.com/iam/docs/creating-managing-service-account-keys",
    setup_label="Get a service account key",
    display_name="Google Vertex AI",
    configuration_model=Config,
    supported_model_apis=("google.generate_content",),
    build_provider=_build_provider,
    credential_model=GoogleServiceAccount,
    reserved_headers=("authorization", "x-goog-api-key", "x-goog-user-project"),
)

if TYPE_CHECKING:
    from pydantic_ai.providers.google_cloud import GoogleCloudProvider
