"""TypeSafe Provider adapter for native Jev classification and scoring Models."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2

from .credentials import ApiKeyCredential
from .definition import ModelProviderDefinition, require_credential, require_endpoint
from .types import ModelConnection, ProviderConfiguration


def _build_provider(
    provider: ModelConnection[ProviderConfiguration, ApiKeyCredential],
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> TypeSafeProvider:
    from pydantic_ai.providers.typesafe import TypeSafeProvider
    from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

    return TypeSafeProvider(
        typesafe_client=AsyncTypeSafeClient(
            api_key=require_credential(provider),
            base_url=require_endpoint(provider),
            http_client=http_client,
            headers=provider.extra_headers,
            retry=RetryPolicy(max_retries=0),
        )
    )


DEFINITION = ModelProviderDefinition(
    type="typesafe",
    display_name="TypeSafe",
    setup_url="https://typesafe.ai",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("typesafe.system_one",),
    build_provider=_build_provider,
    endpoint="https://api.typesafe.ai",
)

if TYPE_CHECKING:
    from pydantic_ai.providers.typesafe import TypeSafeProvider
