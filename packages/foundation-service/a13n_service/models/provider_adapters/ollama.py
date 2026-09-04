"""Ollama Provider adapter."""

from collections.abc import Mapping
from typing import Annotated

import httpx2
from pydantic import StringConstraints
from pydantic_ai.providers.ollama import OllamaProvider

from .base import (
    JsonModelDiscoveryAdapter,
    ModelListRequest,
    ModelListSchema,
    ProviderIntegration,
    join_url,
    require_endpoint,
)
from .types import ProviderConfiguration, RuntimeProvider


class Config(ProviderConfiguration):
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _pydantic_provider_name: str,
) -> OllamaProvider:
    return OllamaProvider(base_url=str(provider.configuration["base_url"]), http_client=http_client)


def _request(provider: RuntimeProvider) -> ModelListRequest:
    endpoint = require_endpoint(provider).removesuffix("/v1")
    return ModelListRequest(url=join_url(endpoint, "api/tags"), headers={})


def _endpoint(configuration: Mapping[str, object]) -> str:
    return str(configuration["base_url"])


INTEGRATION = ProviderIntegration(
    type="ollama",
    display_name="Ollama",
    configuration_model=Config,
    supported_model_apis=("ollama.chat_completions",),
    build_provider=_build_provider,
    credential_format=None,
    credential_required=False,
    endpoint=_endpoint,
    endpoint_configuration_field="base_url",
    model_discovery=JsonModelDiscoveryAdapter(
        request_builder=_request,
        schema=ModelListSchema(collection_field="models", identifier_field="name"),
    ),
)
