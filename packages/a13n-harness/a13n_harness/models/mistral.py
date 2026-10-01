"""Native Mistral requests with isolated Host and per-request headers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from mistralai.client import Mistral
from mistralai.client.chat import Chat
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.mistral import MistralModel as NativeMistralModel
from pydantic_ai.settings import ModelSettings, merge_model_settings
from pydantic_ai.tools import RunContext

_request_headers: ContextVar[Mapping[str, str] | None] = ContextVar("mistral_request_headers", default=None)


class _Chat(Chat):
    """Add headers at the SDK boundary without copying its request serialization."""

    def __init__(self, client: Mistral, headers: Mapping[str, str]) -> None:
        super().__init__(client.sdk_configuration, parent_ref=client)
        self._headers = dict(headers)

    def _with_headers(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        # Preserve native SDK header casing while merging overrides case-insensitively.
        headers: dict[str, tuple[str, str]] = {}
        for source in (self._headers, kwargs.get("http_headers") or {}, _request_headers.get() or {}):
            headers.update((key.lower(), (key, value)) for key, value in source.items())
        return {**kwargs, "http_headers": dict(headers.values())}

    async def complete_async(self, *args: Any, **kwargs: Any) -> Any:
        return await super().complete_async(*args, **self._with_headers(kwargs))

    async def stream_async(self, *args: Any, **kwargs: Any) -> Any:
        return await super().stream_async(*args, **self._with_headers(kwargs))


def with_headers(client: Mistral, headers: Mapping[str, str]) -> Mistral:
    client.chat = _Chat(client, headers)
    return client


class MistralModel(NativeMistralModel):
    """Preserve native models and settings while forwarding generic extra_headers."""

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        settings = merge_model_settings(self.settings, model_settings)
        token = _request_headers.set((settings or {}).get("extra_headers"))
        try:
            return await super().request(messages, model_settings, model_request_parameters)
        finally:
            _request_headers.reset(token)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        settings = merge_model_settings(self.settings, model_settings)
        token = _request_headers.set((settings or {}).get("extra_headers"))
        try:
            async with super().request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as response:
                yield response
        finally:
            _request_headers.reset(token)
