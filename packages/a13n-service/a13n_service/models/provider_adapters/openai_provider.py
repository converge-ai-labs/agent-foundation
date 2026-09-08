"""Shared native Provider construction for OpenAI-compatible vendors."""

from collections.abc import Callable, Mapping
from typing import Any

import httpx2
from openai import AsyncOpenAI
from pydantic_ai.providers import Provider

from .base import (
    DiscoveredModelIdentity,
    JsonModelDiscoveryAdapter,
    ModelListRequest,
    ModelListSchema,
    ProviderOperationError,
    require_credential,
    require_endpoint,
)
from .types import RuntimeProvider


def build[NativeOpenAIProvider: Provider[AsyncOpenAI]](
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _model_api: str,
    provider_factory: Callable[..., NativeOpenAIProvider],
) -> NativeOpenAIProvider:
    client = AsyncOpenAI(
        max_retries=0,
        api_key=require_credential(provider),
        base_url=require_endpoint(provider),
        http_client=http_client,
    )
    return provider_factory(openai_client=client)


class OpenAIModelDiscovery(JsonModelDiscoveryAdapter):
    def next_page(self, payload: Mapping[str, Any]) -> dict[str, str]:
        if payload.get("has_more"):
            last = payload.get("last_id")
            if not isinstance(last, str) or not last:
                raise ProviderOperationError("the Provider omitted its continuation token")
            return {"after": last}
        return super().next_page(payload)

    def parse(self, payload: Any) -> list[DiscoveredModelIdentity]:
        return [
            item
            for item in super().parse(payload)
            if not item.upstream_model.startswith(("text-embedding-", "whisper-", "tts-", "omni-moderation-"))
            and item.metadata.get("type") not in ("embedding", "rerank", "moderation", "transcription")
        ]


def openai_style_discovery(
    request_builder: Callable[[RuntimeProvider], ModelListRequest],
) -> JsonModelDiscoveryAdapter:
    return OpenAIModelDiscovery(
        request_builder=request_builder,
        schema=ModelListSchema(
            collection_field="data",
            identifier_field="id",
            display_name_fields=("name",),
        ),
    )
