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
from .types import ProviderConfig, RuntimeProvider


class Config(ProviderConfig):
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _pydantic_provider_name: str,
) -> OllamaProvider:
    return OllamaProvider(base_url=str(provider.config["base_url"]), http_client=http_client)


def _request(provider: RuntimeProvider) -> ModelListRequest:
    endpoint = require_endpoint(provider).removesuffix("/v1")
    return ModelListRequest(url=join_url(endpoint, "api/tags"), headers={})


def _endpoint(config: Mapping[str, object]) -> str:
    return str(config["base_url"])


INTEGRATION = ProviderIntegration(
    key="ollama",
    display_name="Ollama",
    config_model=Config,
    supported_model_apis=("ollama.chat_completions",),
    build_provider=_build_provider,
    credential_format=None,
    credential_required=False,
    endpoint=_endpoint,
    endpoint_config_field="base_url",
    model_discovery=JsonModelDiscoveryAdapter(
        request_builder=_request,
        schema=ModelListSchema(collection_field="models", identifier_field="name"),
    ),
)
