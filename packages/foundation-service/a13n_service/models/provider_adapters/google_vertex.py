"""Google Vertex AI Provider adapter."""

from typing import Annotated

import httpx2
from pydantic import StringConstraints
from pydantic_ai.providers.google_cloud import GoogleCloudProvider

from ..credentials import parse_google_service_account
from .base import ProviderIntegration, require_credential
from .types import CredentialFormat, ProviderConfig, RuntimeProvider


class Config(ProviderConfig):
    project_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    location: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _pydantic_provider_name: str,
) -> GoogleCloudProvider:
    return GoogleCloudProvider(
        credentials=parse_google_service_account(require_credential(provider)),
        project=str(provider.config["project_id"]),
        location=str(provider.config["location"]),
        http_client=http_client,
    )


INTEGRATION = ProviderIntegration(
    key="google_vertex",
    display_name="Google Vertex AI",
    config_model=Config,
    supported_model_apis=("google.generate_content",),
    build_provider=_build_provider,
    credential_format=CredentialFormat.google_service_account_json,
)
