"""Reusable native-media Toolset over a Host-selected reader."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated, Protocol

from pydantic import Field
from pydantic_ai import BinaryContent, RunContext, ToolReturn, VideoUrl
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness.context import AgentContext
from a13n_harness.errors import RunError
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import ToolFailure, tool_failure

_MEDIA_INSTRUCTION = tool_instruction("read_media")

if TYPE_CHECKING:
    from a13n_harness.capabilities.media import (
        MediaConfiguration,
        MediaReadRequest,
        MediaResource,
    )


type MediaToolResult = ToolReturn | ToolFailure


class MediaToolReader(Protocol):
    async def read(self, request: MediaReadRequest) -> MediaResource: ...


class MediaToolset:
    """Standard media-read semantics reusable with a natural reader port."""

    def __init__(
        self,
        reader: MediaToolReader,
        configuration: MediaConfiguration,
    ) -> None:
        self._reader = reader
        self._configuration = configuration

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        max_output_bytes = max(
            self._configuration.max_image_bytes,
            self._configuration.max_video_bytes,
            self._configuration.max_audio_bytes,
        )
        tool = HarnessTool(
            self.read_media,
            harness_metadata=HarnessToolMetadata(
                tool_id="media.read",
                effects=frozenset({"read", "external_communication"}),
                credential_audiences=(),
                idempotency="read_only",
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=256 * 1024,
                    max_output_bytes=max_output_bytes,
                    overflow="fail",
                    redact=True,
                ),
            ),
            name="read_media",
            description=(
                "Read an HTTP/HTTPS image, video, or audio URL as native model-consumable media. "
                "Use instructions for focused analysis."
            ),
        )
        return InstructionFunctionToolset(
            tools=[tool],
            id="a13n-media-tool-functions",
            instructions=_MEDIA_INSTRUCTION,
        )

    async def read_media(
        self,
        ctx: RunContext[AgentContext],
        url: Annotated[str, Field(description="HTTP or HTTPS media URL")],
        instructions: Annotated[str | None, Field(description="Focused analysis instructions")] = None,
    ) -> MediaToolResult:
        from a13n_harness.capabilities.media import (
            MediaReadError,
            MediaReadRequest,
            MediaResource,
        )

        try:
            ctx.deps.configuration.authorize_url(url)
            request = MediaReadRequest(
                url=url,
                instructions=instructions,
                max_image_bytes=self._configuration.max_image_bytes,
                max_video_bytes=self._configuration.max_video_bytes,
                max_audio_bytes=self._configuration.max_audio_bytes,
                allow_direct_video_url=(
                    self._configuration.allow_direct_video_urls and ctx.deps.configuration.allowed_hosts is None
                ),
                configuration=ctx.deps.configuration,
            )
            async with asyncio.timeout(self._configuration.deadline_seconds):
                raw = await self._reader.read(request)
            resource = MediaResource.model_validate(raw)
            ctx.deps.configuration.authorize_url(resource.source_url)
            if resource.direct_url is not None:
                ctx.deps.configuration.authorize_url(resource.direct_url)
                if ctx.deps.configuration.allowed_hosts is not None:
                    return _media_error("direct_media_url_disabled")
            for usage in resource.usage:
                await ctx.deps.record_provider_usage(
                    usage,
                    source="media.reader",
                    tool_id="media.read",
                    tool_call_id=ctx.tool_call_id,
                )
            limit = self._configuration.limit_for(resource.kind)
            if resource.data is not None and len(resource.data) > limit:
                return _media_error("media_too_large", limit=limit)
            if resource.direct_url is not None and not self._configuration.allow_direct_video_urls:
                return _media_error("direct_media_url_disabled")
            content: BinaryContent | VideoUrl
            if resource.data is not None:
                content = BinaryContent(data=resource.data, media_type=resource.media_type)
            else:
                assert resource.direct_url is not None
                content = VideoUrl(url=resource.direct_url, media_type=resource.media_type)
            message = f"The {resource.kind} is attached in this tool result.\n\nCanonical source: {resource.source_url}"
            return ToolReturn(return_value=message, content=[content])
        except TimeoutError:
            return _media_error("media_timeout", retry_hint="retry")
        except RunError:
            raise
        except MediaReadError as exc:
            return _media_error(exc.code)
        except (TypeError, ValueError):
            return _media_error("media_response_invalid")
        except Exception:
            return _media_error("media_read_failed")


def _media_error(
    code: str,
    *,
    limit: int | None = None,
    retry_hint: str = "dependency_change",
) -> ToolFailure:
    message = {
        "media_binding_missing": "No media reader is configured for this Run.",
        "media_response_invalid": "The media reader returned an invalid result.",
        "media_timeout": "Reading the media timed out.",
        "media_too_large": "The media exceeds the configured inline byte limit.",
    }.get(code, "The media could not be read; check the resource and configured reader.")
    result = tool_failure(code, message, retry_hint=retry_hint)
    if limit is not None:
        result["error"]["max_bytes"] = limit
    return result


__all__ = ["MediaToolResult", "MediaToolset"]
