"""Google Gemini Developer API Provider adapter."""

import httpx2
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from ..domain import ModelExecutionSnapshot
from .base import (
    BuiltModel,
    JsonModelDiscoveryAdapter,
    ModelListRequest,
    ModelListSchema,
    ProviderAdapter,
    join_url,
    model_name,
    require_api,
    require_credential,
    require_endpoint,
)
from .types import EmptyProviderConfig, ProviderType, RuntimeProvider


def _build(snapshot: ModelExecutionSnapshot, provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> BuiltModel:
    require_api(snapshot, "google.generate_content")
    return GoogleModel(
        model_name(snapshot),
        provider=GoogleProvider(
            api_key=require_credential(provider),
            base_url=provider.endpoint,
            http_client=http_client,
        ),
    )


def _request(provider: RuntimeProvider) -> ModelListRequest:
    return ModelListRequest(
        url=join_url(require_endpoint(provider), "v1beta/models"),
        headers={"x-goog-api-key": require_credential(provider)},
    )


ADAPTER = ProviderAdapter(
    build_model=_build,
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

TYPE = ProviderType(
    key="google_gemini",
    display_name="Google Gemini",
    config_model=EmptyProviderConfig,
    supported_model_apis=("google.generate_content",),
    endpoint="https://generativelanguage.googleapis.com",
    supports_model_discovery=True,
)
