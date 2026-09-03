"""Ollama Provider adapter."""

from collections.abc import Mapping
from typing import Annotated

import httpx2
from pydantic import StringConstraints
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider

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
    require_endpoint,
)
from .types import ProviderConfig, ProviderType, RuntimeProvider


class Config(ProviderConfig):
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


def _build(snapshot: ModelExecutionSnapshot, provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> BuiltModel:
    require_api(snapshot, "ollama.chat_completions")
    return OllamaModel(
        model_name(snapshot),
        provider=OllamaProvider(base_url=str(provider.config["base_url"]), http_client=http_client),
    )


def _request(provider: RuntimeProvider) -> ModelListRequest:
    endpoint = require_endpoint(provider).removesuffix("/v1")
    return ModelListRequest(url=join_url(endpoint, "api/tags"), headers={})


ADAPTER = ProviderAdapter(
    build_model=_build,
    model_discovery=JsonModelDiscoveryAdapter(
        request_builder=_request,
        schema=ModelListSchema(collection_field="models", identifier_field="name"),
    ),
)


def _endpoint(config: Mapping[str, object]) -> str:
    return str(config["base_url"])


TYPE = ProviderType(
    key="ollama",
    display_name="Ollama",
    config_model=Config,
    supported_model_apis=("ollama.chat_completions",),
    credential_format=None,
    credential_required=False,
    endpoint=_endpoint,
    endpoint_config_field="base_url",
    supports_model_discovery=True,
)
