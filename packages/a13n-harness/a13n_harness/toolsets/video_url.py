"""Attach supported video URLs as native model input without media acquisition."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import Field
from pydantic_ai import RunContext, ToolReturn, VideoUrl
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness._video_urls import video_url_error
from a13n_harness.context import AgentContext
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import ToolFailure, tool_failure

if TYPE_CHECKING:
    from a13n_harness.spec import HarnessModelCharacteristics


class VideoUrlToolset:
    """Native-only video URL tool; local media remains the file view tool's concern."""

    def get_toolset(self, characteristics: HarnessModelCharacteristics) -> FunctionToolset[AgentContext]:
        from a13n_harness.spec import ModelCapability

        kinds = []
        if ModelCapability.YOUTUBE_URL_UNDERSTANDING in characteristics.capabilities:
            kinds.append("YouTube video links")
        if ModelCapability.VIDEO_URL_UNDERSTANDING in characteristics.capabilities:
            kinds.append("direct HTTP(S) video resource URLs")
        supported = ", ".join(kinds)
        tool = HarnessTool(
            self.read_video_url,
            name="read_video_url",
            description=(
                "Attach a video URL for the current model to analyze. "
                f"Supported URL types: {supported}. This tool does not download media. "
                "Use instructions for focused analysis."
            ),
            harness_metadata=HarnessToolMetadata(
                tool_id="media.read_video_url",
                effects=frozenset({"read", "external_communication"}),
                credential_audiences=(),
                idempotency="read_only",
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=256 * 1024,
                    max_output_bytes=256 * 1024,
                    overflow="fail",
                    redact=True,
                ),
            ),
        )
        return InstructionFunctionToolset(
            tools=[tool],
            id="a13n-video-url-functions",
            instructions=tool_instruction("read_video_url"),
        )

    async def read_video_url(
        self,
        ctx: RunContext[AgentContext],
        url: Annotated[str, Field(min_length=1, max_length=16 * 1024, description="HTTP(S) video URL")],
        instructions: Annotated[
            str | None, Field(max_length=64 * 1024, description="Focused video analysis instructions")
        ] = None,
        media_type: Annotated[
            str | None, Field(max_length=256, description="Video MIME type when the URL has no recognizable extension")
        ] = None,
    ) -> ToolReturn | ToolFailure:
        if "\x00" in (instructions or ""):
            return tool_failure("video_url_invalid", "Video analysis instructions must not contain NUL.")
        video = VideoUrl(url=url, media_type=media_type)
        error = video_url_error(video, ctx.deps.model_characteristics)
        if error is not None:
            message = (
                "The current model does not support this video URL type. Use a supported URL or view a local video file."
                if error == "video_url_unsupported"
                else "Provide a safe HTTP(S) video URL and a video MIME type when it cannot be inferred."
            )
            return tool_failure(error, message, retry_hint="request_change")
        message = f"The video is attached as native model input.\n\nSource: {url}"
        if instructions:
            message += f"\n\nAnalysis instructions: {instructions}"
        return ToolReturn(return_value=message, content=[video])


__all__ = ["VideoUrlToolset"]
