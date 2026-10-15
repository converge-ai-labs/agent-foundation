"""Google Vertex AI Provider adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import httpx2
from pydantic import ConfigDict, Field, JsonValue, RootModel, StringConstraints

from .credentials import ApiKeyCredential, GoogleServiceAccount
from .definition import ModelProviderDefinition
from .types import ModelConnection, ProviderConfiguration


class Config(ProviderConfiguration):
    project_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)] | None = Field(
        default=None, description="Required for service-account authentication; omit when using an API key."
    )
    location: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")] | None = Field(
        default=None, description="Required for service-account authentication; omit when using an API key."
    )


class Credential(
    RootModel[
        Annotated[ApiKeyCredential, Field(title="API key")]
        | Annotated[GoogleServiceAccount, Field(title="Service account")]
    ]
):
    model_config = ConfigDict(frozen=True, json_schema_extra={"type": "object"})


class VertexDefinition(ModelProviderDefinition[Config, Credential]):
    def parse_credential(self, configuration: Config | dict[str, JsonValue], credential: object) -> Credential | None:
        parsed = super().parse_credential(configuration, credential)
        config = Config.model_validate(configuration)
        if parsed is not None:
            if isinstance(parsed.root, ApiKeyCredential):
                if config.project_id is not None or config.location is not None:
                    raise ValueError("Vertex API-key authentication does not accept project_id or location")
            elif config.project_id is None or config.location is None:
                raise ValueError("Vertex service-account authentication requires project_id and location")
        return parsed


def _build_provider(
    provider: ModelConnection[Config, Credential],
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> GoogleCloudProvider:
    from google.genai import Client
    from pydantic_ai.providers.google_cloud import GoogleCloudProvider

    from .google_provider import http_options

    assert provider.credential is not None
    credential = provider.credential.root
    if isinstance(credential, ApiKeyCredential):
        client = Client(
            vertexai=True,
            api_key=credential.api_key.get_secret_value(),
            http_options=http_options(provider, http_client),
        )
    else:
        client = Client(
            vertexai=True,
            credentials=credential.native_credentials(),
            project=provider.configuration.project_id,
            location=provider.configuration.location,
            http_options=http_options(provider, http_client),
        )
    return GoogleCloudProvider(client=client)


DEFINITION = VertexDefinition(
    type="google_vertex",
    catalog_providers=("google-vertex",),
    setup_url="https://cloud.google.com/vertex-ai/generative-ai/docs/start/gcp-auth",
    setup_label="Set up Vertex AI",
    display_name="Google Vertex AI",
    configuration_model=Config,
    supported_model_apis=("google.generate_content",),
    build_provider=_build_provider,
    credential_model=Credential,
    reserved_headers=("authorization", "x-goog-api-key", "x-goog-user-project"),
)

if TYPE_CHECKING:
    from pydantic_ai.providers.google_cloud import GoogleCloudProvider
