"""Default bounded video URL acquisition and request video filtering."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import copy
from dataclasses import dataclass

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from a13n_harness._video_urls import supports_video_urls
from a13n_harness.context import AgentContext
from a13n_harness.filters.video_url import project_video_urls
from a13n_harness.toolsets.video_url import VideoUrlToolset

VIDEO_URL_CAPABILITY_ID = "a13n.video_url"


@dataclass(init=False)
class VideoUrlCapability(AbstractCapability[AgentContext]):
    """Builder-owned video input support gated by captured model characteristics."""

    id = VIDEO_URL_CAPABILITY_ID

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost")

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return DynamicToolset(self._toolset_for_run, per_run_step=False, id="a13n-video-url-tools")

    async def _toolset_for_run(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext] | None:
        if not supports_video_urls(ctx.deps.model_characteristics):
            return None
        assert ctx.deps.model_characteristics is not None
        return VideoUrlToolset().get_toolset(ctx.deps.model_characteristics)

    async def wrap_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        handler: Callable[[ModelRequestContext], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        projected = project_video_urls(request_context.messages, ctx.deps.model_characteristics)
        if projected is None:
            return await handler(request_context)
        updated = copy(request_context)
        updated.messages = projected
        return await handler(updated)


__all__ = ["VideoUrlCapability"]
