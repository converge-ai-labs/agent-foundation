"""Shared native Provider construction for OpenAI-compatible vendors."""

from collections.abc import Callable

import httpx2
from openai import AsyncOpenAI
from pydantic_ai.providers import Provider

from .base import require_credential, require_endpoint
from .types import RuntimeProvider


def build[NativeOpenAIProvider: Provider[AsyncOpenAI]](
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _pydantic_provider_name: str,
    provider_factory: Callable[..., NativeOpenAIProvider],
) -> NativeOpenAIProvider:
    client = AsyncOpenAI(
        api_key=require_credential(provider),
        base_url=require_endpoint(provider),
        http_client=http_client,
    )
    return provider_factory(openai_client=client)
