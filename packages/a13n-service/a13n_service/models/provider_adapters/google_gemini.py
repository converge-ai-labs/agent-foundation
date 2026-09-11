"""Google Gemini Developer API Provider adapter."""

from collections.abc import Mapping
from typing import Any

import httpx2
from google.genai import Client
from pydantic_ai.providers.google import GoogleProvider

from ..descriptions import positive_token_limit
from ..domain import ModelCandidate, ModelLimits
from .base import (
    DiscoveredModelIdentity,
    JsonModelDiscoveryAdapter,
    ModelListRequest,
    ModelListSchema,
    ProviderIntegration,
    ProviderOperationError,
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


def _request(provider: RuntimeProvider) -> ModelListRequest:
    return ModelListRequest(
        url=join_url(require_endpoint(provider), "v1beta/models"),
        headers={"x-goog-api-key": require_credential(provider)},
    )


class GeminiDiscovery(JsonModelDiscoveryAdapter):
    def parse(self, payload: Any) -> list[DiscoveredModelIdentity]:
        return [
            item
            for item in super().parse(payload)
            if not isinstance(item.metadata.get("supportedGenerationMethods"), list)
            or "generateContent" in item.metadata["supportedGenerationMethods"]
        ]

    def next_page(self, payload: Mapping[str, Any]) -> dict[str, str]:
        token = payload.get("nextPageToken")
        if token:
            if not isinstance(token, str):
                raise ProviderOperationError("the Provider returned an invalid continuation token")
            return {"pageToken": token}
        return super().next_page(payload)

    def describe(
        self, model_api: str, upstream_model: str, display_name: str | None, metadata: Mapping[str, Any]
    ) -> ModelCandidate:
        result = super().describe(model_api, upstream_model, display_name, metadata)
        limits = ModelLimits(
            context_window_tokens=positive_token_limit(metadata.get("inputTokenLimit")),
            max_output_tokens=positive_token_limit(metadata.get("outputTokenLimit")),
        )
        return result.model_copy(update={"limits": limits})


INTEGRATION = ProviderIntegration(
    type="google_gemini",
    display_name="Google Gemini",
    configuration_model=ProviderConfiguration,
    supported_model_apis=("google.generate_content",),
    build_provider=_build_provider,
    endpoint="https://generativelanguage.googleapis.com",
    reserved_headers=("x-goog-api-key",),
    model_discovery=GeminiDiscovery(
        request_builder=_request,
        schema=ModelListSchema(
            collection_field="models",
            identifier_field="name",
            display_name_fields=("displayName",),
            identifier_prefix="models/",
        ),
    ),
)
