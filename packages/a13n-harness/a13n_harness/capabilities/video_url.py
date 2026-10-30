"""Default bounded media URL acquisition and request video filtering."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from a13n_harness.context import AgentContext
from a13n_harness.filters.video_url import project_video_urls
from a13n_harness.model_context import ModelContextCoordinatorCapability
from a13n_harness.toolsets.media_url import MediaUrlToolset

VIDEO_URL_CAPABILITY_ID = "a13n.video_url"


@dataclass(init=False)
class VideoUrlCapability(AbstractCapability[AgentContext]):
    """Builder-owned video input support gated by captured model characteristics."""

    id = VIDEO_URL_CAPABILITY_ID

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost", wrapped_by=(ModelContextCoordinatorCapability,))

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return DynamicToolset(self._toolset_for_run, per_run_step=False, id="a13n-video-url-tools")

    async def _toolset_for_run(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext] | None:
        return MediaUrlToolset().get_toolset(ctx)

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        projected = project_video_urls(request_context.messages, ctx.deps.model_characteristics)
        if projected is None:
            return request_context
        updated = copy(request_context)
        updated.messages = projected
        return updated


__all__ = ["VideoUrlCapability"]
