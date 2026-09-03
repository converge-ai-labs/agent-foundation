"""Google Vertex AI Provider adapter."""

from typing import Annotated

import httpx2
from pydantic import StringConstraints
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google_cloud import GoogleCloudProvider

from ..credentials import parse_google_service_account
from ..domain import ModelExecutionSnapshot
from .base import BuiltModel, ProviderAdapter, model_name, require_api, require_credential
from .types import CredentialFormat, ProviderConfig, ProviderType, RuntimeProvider


class Config(ProviderConfig):
    project_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    location: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]


def _build(snapshot: ModelExecutionSnapshot, provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> BuiltModel:
    require_api(snapshot, "google.generate_content")
    return GoogleModel(
        model_name(snapshot),
        provider=GoogleCloudProvider(
            credentials=parse_google_service_account(require_credential(provider)),
            project=str(provider.config["project_id"]),
            location=str(provider.config["location"]),
            http_client=http_client,
        ),
    )


ADAPTER = ProviderAdapter(build_model=_build)

TYPE = ProviderType(
    key="google_vertex",
    display_name="Google Vertex AI",
    config_model=Config,
    supported_model_apis=("google.generate_content",),
    credential_format=CredentialFormat.google_service_account_json,
)
