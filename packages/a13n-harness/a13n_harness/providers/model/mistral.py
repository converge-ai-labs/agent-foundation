"""Mistral's native Chat Completions Provider."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2

from .credentials import ApiKeyCredential
from .definition import (
    ConnectionProbeRequest,
    ModelProviderDefinition,
    join_url,
    require_credential,
    require_endpoint,
)
from .types import ModelConnection, ProviderConfiguration


def _build_provider(
    provider: ModelConnection[ProviderConfiguration, ApiKeyCredential],
    http_client: httpx2.AsyncClient,
    _model_api: str,
) -> MistralProvider:
    from mistralai.client import Mistral
    from pydantic_ai.providers.mistral import MistralProvider

    from ...models.mistral import with_headers

    return MistralProvider(
        mistral_client=with_headers(
            Mistral(
                api_key=require_credential(provider),
                server_url=require_endpoint(provider),
                async_client=http_client,  # pyright: ignore[reportArgumentType] -- SDK protocol accepts httpx2.
                retry_config=None,
                timeout_ms=int(http_client.timeout.read * 1000) if http_client.timeout.read is not None else None,
            ),
            provider.extra_headers,
        )
    )


def _request(provider: ModelConnection[ProviderConfiguration, ApiKeyCredential]) -> ConnectionProbeRequest:
    return ConnectionProbeRequest(
        url=join_url(require_endpoint(provider), "v1/models"),
        headers={"authorization": f"Bearer {require_credential(provider)}"},
    )


DEFINITION = ModelProviderDefinition(
    type="mistral",
    catalog_providers=("mistral",),
    setup_url="https://console.mistral.ai/api-keys",
    display_name="Mistral",
    configuration_model=ProviderConfiguration,
    credential_model=ApiKeyCredential,
    supported_model_apis=("mistral.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://api.mistral.ai",
    connection_probe=_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.mistral import MistralProvider
