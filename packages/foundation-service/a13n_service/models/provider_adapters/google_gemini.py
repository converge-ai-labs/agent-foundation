"""Google Gemini Developer API Provider adapter."""

import httpx2
from pydantic_ai.providers.google import GoogleProvider

from .base import (
    JsonModelDiscoveryAdapter,
    ModelListRequest,
    ModelListSchema,
    ProviderIntegration,
    join_url,
    require_credential,
    require_endpoint,
)
from .types import EmptyProviderConfig, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _pydantic_provider_name: str,
) -> GoogleProvider:
    return GoogleProvider(
        api_key=require_credential(provider),
        base_url=provider.endpoint,
        http_client=http_client,
    )


def _request(provider: RuntimeProvider) -> ModelListRequest:
    return ModelListRequest(
        url=join_url(require_endpoint(provider), "v1beta/models"),
        headers={"x-goog-api-key": require_credential(provider)},
    )


INTEGRATION = ProviderIntegration(
    key="google_gemini",
    display_name="Google Gemini",
    config_model=EmptyProviderConfig,
    supported_model_apis=("google.generate_content",),
    build_provider=_build_provider,
    endpoint="https://generativelanguage.googleapis.com",
    model_discovery=JsonModelDiscoveryAdapter(
        request_builder=_request,
        schema=ModelListSchema(
            collection_field="models",
            identifier_field="name",
            display_name_fields=("displayName",),
            identifier_prefix="models/",
        ),
    ),
)
