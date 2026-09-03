"""Shared implementation for OpenAI Chat Completions-compatible vendors."""

from collections.abc import Callable

import httpx2
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers import Provider

from ..domain import ModelExecutionSnapshot
from .base import BuiltModel, model_name, openai_client, require_api
from .types import RuntimeProvider


def build(
    snapshot: ModelExecutionSnapshot,
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    provider_factory: Callable[..., Provider[AsyncOpenAI]],
) -> BuiltModel:
    require_api(snapshot, "openai.chat_completions")
    native_provider = provider_factory(openai_client=openai_client(provider, http_client))
    return OpenAIChatModel(model_name(snapshot), provider=native_provider)
