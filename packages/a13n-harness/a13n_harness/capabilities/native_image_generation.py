"""Native image generation with Host-owned file saving."""

from __future__ import annotations

from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import WrapRunHandler
from pydantic_ai.messages import (
    AgentStreamEvent,
    FilePart,
    ModelResponse,
    PartEndEvent,
    PartStartEvent,
    TextPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.native_tools import ImageGenerationTool
from pydantic_ai.run import AgentRunResult

from a13n_harness.context import AgentContext

NativeImageSaver = Callable[[RunContext[AgentContext], FilePart], Awaitable[str]]


def _image_key(part: FilePart) -> tuple[str, str]:
    return part.content.media_type, sha256(part.content.data).hexdigest()


def _is_image(part: object) -> bool:
    return isinstance(part, FilePart) and part.content.media_type.startswith("image/")


@dataclass
class NativeImageGenerationCapability(AbstractCapability[AgentContext]):
    """Register ImageGenerationTool and save its images through the Host.

    The required async saver returns the saved file's model-visible path or URL.
    Tool settings and provider support remain native Pydantic AI semantics; this
    capability adds no image API client or generation fallback. Only completed
    responses are saved; previews are withheld and saver failures fail the Run.
    """

    saver: NativeImageSaver
    tool: ImageGenerationTool = field(default_factory=ImageGenerationTool)
    _saved: dict[tuple[str, str], TextPart] = field(default_factory=dict, init=False, repr=False)
    _pending: list[tuple[int, TextPart]] = field(default_factory=list, init=False, repr=False)

    @classmethod
    def get_serialization_name(cls) -> None:
        return None  # The saver is trusted process-local code, not configuration.

    def get_native_tools(self) -> list[ImageGenerationTool]:
        return [self.tool]

    def get_instructions(self) -> str:
        return (
            "When presenting a saved generated image in your reply, display it with Markdown image syntax: "
            "![brief image description](<saved path or URL>). Use the exact path or URL returned by the image "
            "saver, not a guessed location or raw image bytes. Do not claim an image was saved if saving failed."
        )

    async def for_run(self, ctx: RunContext[AgentContext]) -> NativeImageGenerationCapability:
        return replace(self)

    async def _save(self, ctx: RunContext[AgentContext], part: FilePart) -> TextPart:
        key = _image_key(part)
        if key not in self._saved:
            reference = await self.saver(ctx, part)
            if not reference.strip():
                raise ValueError("Native image saver must return a non-empty path or URL")
            self._saved[key] = TextPart(f"\n\nGenerated image: {reference}\n")
        return self._saved[key]

    async def wrap_run_event_stream(
        self,
        ctx: RunContext[AgentContext],
        *,
        stream: AsyncIterable[AgentStreamEvent],
    ) -> AsyncIterable[AgentStreamEvent]:
        # after_model_request finishes before the tool/output node's stream.
        # Publish its saved references there, not from provisional image events.
        pending, self._pending = self._pending, []
        for index, part in pending:
            yield PartStartEvent(index=index, part=part)
            yield PartEndEvent(index=index, part=part)
        async for event in stream:
            if isinstance(event, PartStartEvent | PartEndEvent) and _is_image(event.part):
                continue
            yield event

    async def after_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        parts = []
        for index, part in enumerate(response.parts):
            if isinstance(part, FilePart) and _is_image(part):
                reference = (
                    await self._save(ctx, part)
                    if response.state == "complete"
                    else TextPart("[Native image was not saved: response is incomplete.]")
                )
                parts.append(reference)
                self._pending.append((index, reference))
            else:
                parts.append(part)
        return replace(response, parts=parts)

    async def wrap_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        handler: WrapRunHandler,
    ) -> AgentRunResult[Any]:
        try:
            return await handler()
        finally:
            # Pydantic retains partial responses on interruption without calling
            # after_model_request. Do not checkpoint their unmaterialized bytes.
            for message in ctx.messages:
                if isinstance(message, ModelResponse):
                    message.parts = [
                        self._saved.get(_image_key(part), TextPart("[Native image was not saved.]"))
                        if isinstance(part, FilePart) and _is_image(part)
                        else part
                        for part in message.parts
                    ]


__all__ = ["NativeImageGenerationCapability", "NativeImageSaver"]
