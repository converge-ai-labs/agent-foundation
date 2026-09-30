"""Keep Harness media presentation metadata out of Google provider parameters."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from copy import copy
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, WrapModelRequestHandler
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolReturnPart,
    UploadedFile,
    UserPromptPart,
    VideoUrl,
)
from pydantic_ai.models import Model, ModelRequestContext, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from a13n_harness.context import AgentContext

MODEL_CONTENT_CAPABILITY_ID = "a13n.model.content"
_PRESENTATION_KEYS = frozenset({"display", "source_id"})


@dataclass(init=False)
class ModelContentCapability(AbstractCapability[AgentContext]):
    """Install request-local media projection at the native Google boundary."""

    id = MODEL_CONTENT_CAPABILITY_ID

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost")

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        del ctx
        # Native token counting precedes wrap_model_request. Install the same
        # idempotent provider projection without changing canonical messages.
        return _project_request_model(request_context)

    async def wrap_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        del ctx
        return await handler(_project_request_model(request_context))


def _project_request_model(request_context: ModelRequestContext) -> ModelRequestContext:
    model = _provider_content_model(request_context.model)
    if model is request_context.model:
        return request_context
    updated = copy(request_context)
    updated.model = model
    return updated


def _provider_content_model(model: Model) -> Model:
    # Project below native wrappers, especially SelfHealingModel: its exact
    # repairs must still mutate canonical history, not our outbound-only copy.
    if isinstance(model, _GoogleContentModel):
        return model
    if isinstance(model, GoogleModel):
        return _GoogleContentModel(model)
    if isinstance(model, WrapperModel):
        wrapped = _provider_content_model(model.wrapped)
        if wrapped is not model.wrapped:
            detached = copy(model)
            detached.wrapped = wrapped
            return detached
    elif isinstance(model, FallbackModel):
        models = [_provider_content_model(item) for item in model.models]
        if any(projected is not original for projected, original in zip(models, model.models, strict=True)):
            detached = copy(model)
            detached.models = models
            return detached
    return model


class _GoogleContentModel(WrapperModel):
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        return await self.wrapped.request(_google_messages(messages), model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        async with self.wrapped.request_stream(
            _google_messages(messages), model_settings, model_request_parameters, run_context
        ) as response:
            yield response

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        return await self.wrapped.count_tokens(_google_messages(messages), model_settings, model_request_parameters)


def _google_messages(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Strip only Harness-owned keys; retain bytes, identity, and provider options."""
    projected = list(messages)
    for message_index, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            continue
        parts = list(message.parts)
        changed = False
        for part_index, part in enumerate(parts):
            if not isinstance(part, UserPromptPart) and type(part) is not ToolReturnPart:
                continue
            content = part.content
            if isinstance(content, BinaryContent | ImageUrl | AudioUrl | VideoUrl | DocumentUrl | UploadedFile):
                items = [content]
            elif isinstance(content, list | tuple):
                items = list(content)
            else:
                continue
            item_changed = False
            for item_index, item in enumerate(items):
                if not isinstance(item, BinaryContent | ImageUrl | AudioUrl | VideoUrl | DocumentUrl | UploadedFile):
                    continue
                metadata = item.vendor_metadata or {}
                if not _PRESENTATION_KEYS.intersection(metadata):
                    continue
                detached = copy(item)
                detached.vendor_metadata = {
                    key: value for key, value in metadata.items() if key not in _PRESENTATION_KEYS
                } or None
                items[item_index] = detached
                item_changed = True
            if item_changed:
                replacement = (
                    items if isinstance(content, list) else tuple(items) if isinstance(content, tuple) else items[0]
                )
                parts[part_index] = replace(part, content=replacement)
                changed = True
        if changed:
            projected[message_index] = replace(message, parts=parts)
    return projected
