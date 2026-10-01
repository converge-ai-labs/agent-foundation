"""Acquire bounded direct video bytes or attach supported native YouTube input."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import httpx2
from pydantic import Field
from pydantic_ai import RunContext, ToolReturn, VideoUrl
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness._urls import require_audience_safe_url
from a13n_harness._video_urls import download_video, video_url_error
from a13n_harness.configuration import RunConfiguration
from a13n_harness.context import AgentContext
from a13n_harness.http import ProviderHttpError
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from a13n_harness.video_input import VideoUrlType

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import ToolFailure, tool_failure

if TYPE_CHECKING:
    from a13n_harness.spec import HarnessModelCharacteristics


class VideoUrlToolset:
    """URL acquisition tool; local media remains the file view tool's concern."""

    def get_toolset(
        self, characteristics: HarnessModelCharacteristics, configuration: RunConfiguration
    ) -> FunctionToolset[AgentContext]:
        from a13n_harness.spec import ModelCapability

        kinds = []
        sections = []
        if VideoUrlType.YOUTUBE in characteristics.url_input.video and configuration.allowed_hosts is None:
            kinds.append("native YouTube links")
            sections.append("read_video_url_youtube")
        if ModelCapability.VIDEO_UNDERSTANDING in characteristics.capabilities:
            kinds.append("bounded direct HTTP(S) video downloads")
            sections.append("read_video_url_inline")
        tool = HarnessTool(
            self.read_video_url,
            name="read_video_url",
            description=(
                f"Attach video input for the current model. Supported types: {', '.join(kinds)}. "
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
            tools=[tool], id="a13n-video-url-functions", instructions=tool_instruction("read_video_url", *sections)
        )

    async def read_video_url(
        self,
        ctx: RunContext[AgentContext],
        url: Annotated[str, Field(min_length=1, max_length=16 * 1024, description="HTTP(S) video URL")],
        instructions: Annotated[
            str | None, Field(max_length=64 * 1024, description="Focused video analysis instructions")
        ] = None,
        media_type: Annotated[
            str | None, Field(max_length=256, description="Video MIME type when the source has no recognizable type")
        ] = None,
    ) -> ToolReturn | ToolFailure:
        from a13n_harness.spec import ModelCapability

        characteristics = ctx.deps.model_characteristics
        try:
            require_audience_safe_url(url)
            if "\x00" in (instructions or "") or len(url) > 16 * 1024:
                raise ValueError("invalid input")
            video = VideoUrl(url=url, media_type=media_type)
            if video.is_youtube:
                error = video_url_error(video, characteristics)
                if error is not None:
                    return tool_failure(
                        error,
                        "The selected transport does not support this native YouTube input.",
                        retry_hint="request_change",
                    )
                if ctx.deps.configuration.allowed_hosts is not None:
                    return tool_failure(
                        "video_url_unsupported",
                        "Native YouTube input is unavailable under restrictive Run network policy.",
                    )
                content = video
            else:
                if characteristics is None or ModelCapability.VIDEO_UNDERSTANDING not in characteristics.capabilities:
                    return tool_failure(
                        "video_input_unsupported",
                        "The current model does not support inline video files.",
                        retry_hint="request_change",
                    )
                content = await download_video(
                    url,
                    media_type=media_type,
                    policy=EndpointPolicy(configuration=ctx.deps.configuration),
                    limits=characteristics.video_input,
                )
        except EndpointPolicyError:
            return tool_failure(
                "video_download_denied", "The video destination or redirect is denied by Run network policy."
            )
        except ProviderHttpError as error:
            return tool_failure(
                "video_input_too_large" if error.code == "response_too_large" else error.code,
                "The resource cannot be attached within the video byte budget or has an invalid video response.",
                retry_hint="request_change",
            )
        except (TimeoutError, httpx2.TimeoutException):
            return tool_failure("video_download_timeout", "The video download timed out.")
        except httpx2.HTTPStatusError as error:
            return tool_failure(
                "video_download_http_error", f"The video source returned HTTP {error.response.status_code}."
            )
        except httpx2.RequestError:
            return tool_failure("video_download_failed", "The video download failed.")
        except (TypeError, ValueError):
            return tool_failure(
                "video_url_invalid",
                "Provide a safe HTTP(S) video URL and a video MIME type.",
                retry_hint="request_change",
            )
        message = f"The video is attached as model input.\n\nSource: {url}"
        if instructions:
            message += f"\n\nAnalysis instructions: {instructions}"
        return ToolReturn(return_value=message, content=[content])


__all__ = ["VideoUrlToolset"]
