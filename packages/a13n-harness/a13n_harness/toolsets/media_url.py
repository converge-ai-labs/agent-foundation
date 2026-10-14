"""Read direct media URLs natively or through configured understanding models."""

from __future__ import annotations

from typing import Annotated

import httpx2
from pydantic import Field
from pydantic_ai import RunContext, ToolReturn, VideoUrl
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness._media_urls import download_media, media_kind
from a13n_harness._urls import require_audience_safe_url
from a13n_harness._video_urls import video_url_error
from a13n_harness.context import AgentContext
from a13n_harness.http import ProviderHttpError
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from a13n_harness.video_input import VideoUrlType

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._media import (
    available_media_kinds,
    dispatch_media,
    media_understanding_error,
    resolve_media_understanding,
    supports_native_media,
)
from ._results import ToolFailure, tool_failure
from .file_media import (
    MediaUnderstandingError,
    MediaUnderstandingRequest,
    NativeInputMediaKind,
    media_source_byte_limit,
)

# Keep the established policy identity when changing the model-facing name.
MEDIA_URL_TOOL_ID = "media.read_video_url"


def supports_youtube(ctx: RunContext[AgentContext]) -> bool:
    characteristics = ctx.deps.model_characteristics
    return (
        characteristics is not None
        and VideoUrlType.YOUTUBE in characteristics.url_input.video
        and ctx.deps.configuration.allowed_hosts is None
    )


class MediaUrlToolset:
    """URL acquisition; local media remains the file view tool's concern."""

    def get_toolset(self, ctx: RunContext[AgentContext]) -> FunctionToolset[AgentContext] | None:
        kinds = available_media_kinds(ctx)
        youtube = supports_youtube(ctx)
        if not kinds and not youtube:
            return None
        descriptions = [f"bounded direct HTTP(S) {kind} downloads" for kind in kinds]
        sections = ["read_media_url_inline"] if kinds else []
        if youtube:
            descriptions.append("native YouTube links")
            sections.append("read_media_url_youtube")
        tool = HarnessTool(
            self.read_media_url,
            name="read_media_url",
            description=(
                f"Read media using native input or a configured understanding model. Supported types: {', '.join(descriptions)}. "
                "Use instructions for focused analysis."
            ),
            harness_metadata=HarnessToolMetadata(
                tool_id=MEDIA_URL_TOOL_ID,
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
            tools=[tool], id="a13n-media-url-functions", instructions=tool_instruction("read_media_url", *sections)
        )

    async def read_media_url(
        self,
        ctx: RunContext[AgentContext],
        url: Annotated[
            str,
            Field(min_length=1, max_length=16 * 1024, description="Direct HTTP(S) media URL or supported YouTube link"),
        ],
        instructions: Annotated[
            str | None, Field(max_length=64 * 1024, description="Focused media analysis instructions")
        ] = None,
        media_type: Annotated[
            str | None,
            Field(
                max_length=256, description="Image, video, or audio MIME type when the source has no recognizable type"
            ),
        ] = None,
    ) -> ToolReturn | ToolFailure:
        characteristics = ctx.deps.model_characteristics

        def byte_limit(kind: NativeInputMediaKind) -> int:
            maximum = media_source_byte_limit(kind)
            if supports_native_media(characteristics, kind):
                if kind == "video" and characteristics is not None:
                    maximum = min(maximum, characteristics.video_input.max_raw_bytes)
            elif kind not in available_media_kinds(ctx):
                raise MediaUnderstandingError("media_understanding_unavailable")
            return maximum

        try:
            require_audience_safe_url(url)
            if "\x00" in (instructions or "") or len(url) > 16 * 1024:
                raise ValueError("invalid input")
            video = VideoUrl(url=url, media_type=media_type)
            if video.is_youtube:
                error = video_url_error(video, characteristics)
                if error is not None or not supports_youtube(ctx):
                    return tool_failure(
                        error or "video_url_unsupported",
                        "Native YouTube input requires explicit model support and unrestricted Run hostname policy.",
                        retry_hint="request_change",
                    )
                message = f"The video is attached as model input.\n\nSource: {url}"
                if instructions:
                    message += f"\n\nAnalysis instructions: {instructions}"
                return ToolReturn(return_value=message, content=[video])
            content = await download_media(
                url,
                media_type=media_type,
                policy=EndpointPolicy(configuration=ctx.deps.configuration),
                byte_limit=byte_limit,
            )
            request = MediaUnderstandingRequest(
                kind=media_kind(content.media_type),
                media_type=content.media_type,
                source_name=url,
                source_bytes=content.data,
                instructions=instructions,
            )
        except MediaUnderstandingError as error:
            return media_understanding_error(error.code)
        except EndpointPolicyError:
            return tool_failure(
                "media_download_denied", "The media destination or redirect is denied by Run network policy."
            )
        except ProviderHttpError as error:
            return tool_failure(
                "media_input_too_large" if error.code == "response_too_large" else error.code,
                "The resource exceeds the media byte budget or has an invalid media response.",
                retry_hint="request_change",
            )
        except (TimeoutError, httpx2.TimeoutException):
            return tool_failure("media_download_timeout", "The media download timed out.")
        except httpx2.HTTPStatusError as error:
            return tool_failure(
                "media_download_http_error", f"The media source returned HTTP {error.response.status_code}."
            )
        except httpx2.RequestError:
            return tool_failure("media_download_failed", "The media download failed.")
        except (TypeError, ValueError):
            return tool_failure(
                "media_url_invalid",
                "Provide a safe HTTP(S) media URL and a media MIME type.",
                retry_hint="request_change",
            )
        return await dispatch_media(
            ctx,
            request,
            resolve=resolve_media_understanding,
            tool_id=MEDIA_URL_TOOL_ID,
            usage_source="media.url_understanding",
        )
