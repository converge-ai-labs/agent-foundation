"""Shared native Provider construction for OpenAI-compatible vendors."""

from collections.abc import Callable

import httpx2
from openai import AsyncOpenAI
from pydantic_ai.providers import Provider

from .base import (
    require_credential,
    require_endpoint,
)
from .types import RuntimeProvider


class ClientEndpointProvider(Provider[AsyncOpenAI]):
    """Keep native vendor behavior while reporting the injected client's real endpoint."""

    @property
    def base_url(self) -> str:
        return str(self.client.base_url)


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
        default_headers=provider.extra_headers,
    )
    return provider_factory(openai_client=client)
