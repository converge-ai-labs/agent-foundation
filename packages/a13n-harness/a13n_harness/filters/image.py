"""Request-only image preparation adapted from ya-agent-sdk's image filters.

Reference: Wh1isper/ya-mono, revision df1550ab74cd182792ef58c3bd0ac90aad38cb50,
packages/ya-agent-sdk/ya_agent_sdk/filters/image.py and utils.py.

Adapted portions retain the upstream BSD 3-Clause notice:

Copyright (c) 2026, wh1isper

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable
from copy import copy, deepcopy
from dataclasses import dataclass, replace
from typing import Any

import anyio.to_thread
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import BinaryContent, BinaryImage, ImageUrl, RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolReturnPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext

from a13n_harness.context import AgentContext

IMAGE_FILTER_CAPABILITY_ID = "a13n.filter.image"
_MAX_PROCESSING_PIXELS = 80_000_000
_MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}
_REMOVED_INVALID = "<system-reminder>This image was removed because it is broken or corrupted.</system-reminder>"
_REMOVED_LIMIT = (
    "<system-reminder>This image was removed because it could not be prepared within the configured image limits. "
    "Try resizing or converting it before viewing it again.</system-reminder>"
)
_REMOVED_GIF = (
    "<system-reminder>This GIF image was removed because the image policy does not support GIF.</system-reminder>"
)


class ImageFilterConfiguration(BaseModel):
    """Image preparation policy for one target model's request envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    split_large_images: bool = True
    image_split_max_height: int = Field(default=4096, gt=0)
    image_split_overlap: int = Field(default=50, ge=0)
    max_image_bytes: int = Field(default=5 * 1024 * 1024, ge=0)
    max_image_dimension: int = Field(default=8000, ge=0)
    max_images: int = Field(default=20, ge=0)
    support_gif: bool = True

    @model_validator(mode="after")
    def _validate_overlap(self) -> ImageFilterConfiguration:
        if self.image_split_overlap >= self.image_split_max_height:
            raise ValueError("image split overlap must be smaller than the segment height")
        return self


@dataclass(init=False)
class ImageFilterCapability(AbstractCapability[AgentContext]):
    """Split, compress and prune images without changing retained history."""

    id = IMAGE_FILTER_CAPABILITY_ID

    def __init__(self, configuration: ImageFilterConfiguration | None = None) -> None:
        self.configuration = (configuration or ImageFilterConfiguration()).model_copy(deep=True)

    def get_ordering(self) -> CapabilityOrdering:
        # Run after committed context projection, but before model adapters and
        # their exact-error recovery. Both ordinary and streaming calls use this seam.
        return CapabilityOrdering(position="innermost")

    async def wrap_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        handler: Callable[[ModelRequestContext], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        del ctx
        projected = await anyio.to_thread.run_sync(_project_images, request_context.messages, self.configuration)
        if projected is None:
            return await handler(request_context)
        updated = copy(request_context)
        updated.messages = projected
        return await handler(updated)


def _project_images(messages: list[ModelMessage], configuration: ImageFilterConfiguration) -> list[ModelMessage] | None:
    # Bytes are immutable and shared by deepcopy; mutable native metadata and
    # message envelopes remain detached from canonical history and original inputs.
    projected = deepcopy(messages)
    changed = False
    for message in projected:
        if not isinstance(message, ModelRequest):
            continue
        parts = list(message.parts)
        for index, part in enumerate(parts):
            items = _image_content_items(part)
            if items is None:
                continue
            assert isinstance(part, (UserPromptPart, ToolReturnPart))
            prepared: list[Any] = []
            for item in items:
                if isinstance(item, BinaryContent) and item.is_image:
                    replacements = _prepare_binary_image(item, configuration)
                    prepared.extend(replacements)
                    changed = changed or len(replacements) != 1 or replacements[0] is not item
                else:
                    prepared.append(item)
            parts[index] = replace(part, content=_restore_shape(part.content, prepared))
        message.parts = parts

    count = 0
    for message in reversed(projected):
        if not isinstance(message, ModelRequest):
            continue
        parts = list(message.parts)
        for index in range(len(parts) - 1, -1, -1):
            part = parts[index]
            items = _image_content_items(part)
            if items is None:
                continue
            assert isinstance(part, (UserPromptPart, ToolReturnPart))
            for item_index in range(len(items) - 1, -1, -1):
                item = items[item_index]
                if not isinstance(item, ImageUrl) and not (isinstance(item, BinaryContent) and item.is_image):
                    continue
                count += 1
                if count > configuration.max_images:
                    items[item_index] = (
                        "<system-reminder>This image was removed because it exceeds the maximum allowed images "
                        f"(max_images={configuration.max_images}).</system-reminder>"
                    )
                    changed = True
                elif (
                    isinstance(item, BinaryContent) and item.media_type == "image/gif" and not configuration.support_gif
                ):
                    # Keep reference ordering: GIF policy follows image counting.
                    items[item_index] = _REMOVED_GIF
                    changed = True
            parts[index] = replace(part, content=_restore_shape(part.content, items))
        message.parts = parts
    return projected if changed else None


def _image_content_items(part: object) -> list[Any] | None:
    if isinstance(part, UserPromptPart):
        if isinstance(part.content, (list, tuple)):
            return list(part.content)
    elif type(part) is ToolReturnPart:
        # Pydantic renders only scalar native media and top-level list media as
        # files. Arbitrary nested tool JSON is not another image-input surface.
        if isinstance(part.content, list):
            return list(part.content)
        if isinstance(part.content, (BinaryContent, ImageUrl)):
            return [part.content]
    return None


def _restore_shape(original: Any, items: list[Any]) -> Any:
    if isinstance(original, tuple):
        return tuple(items)
    if isinstance(original, list) or len(items) != 1:
        return items
    return items[0]


def _prepare_binary_image(item: BinaryContent, configuration: ImageFilterConfiguration) -> list[BinaryContent | str]:
    try:
        with Image.open(io.BytesIO(item.data)) as source:
            source.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        return [_REMOVED_INVALID]
    try:
        with Image.open(io.BytesIO(item.data)) as source:
            media_type = _MEDIA_TYPES.get(source.format or "", item.media_type)
            animated = getattr(source, "n_frames", 1) > 1
            split = configuration.split_large_images and source.height > configuration.image_split_max_height
            raw_limit = (configuration.max_image_bytes // 4) * 3 if configuration.max_image_bytes else None
            oversized = (raw_limit is not None and len(item.data) > raw_limit) or (
                configuration.max_image_dimension > 0 and max(source.size) > configuration.max_image_dimension
            )
            if not animated and (split or oversized) and source.width * source.height > _MAX_PROCESSING_PIXELS:
                return [_REMOVED_LIMIT]
            if animated:
                if oversized:
                    return [_REMOVED_LIMIT]
                # Segmentation and JPEG encoding must not discard animation.
                return [_replace_image(item, item.data, media_type)]
            if split:
                segments: list[BinaryContent | str] = []
                top = 0
                step = configuration.image_split_max_height - configuration.image_split_overlap
                while top < source.height:
                    bottom = min(top + configuration.image_split_max_height, source.height)
                    with source.crop((0, top, source.width, bottom)) as segment:
                        data = _encode_segment(segment)
                    native = _replace_image(
                        item, data, "image/png", identifier=f"{item.identifier}-segment-{len(segments) + 1}"
                    )
                    segments.append(_compress_image(native, configuration))
                    if bottom == source.height:
                        break
                    top += step
                return segments
            normalized = _replace_image(item, item.data, media_type)
            return [_compress_image(normalized, configuration)]
    except (OSError, ValueError, Image.DecompressionBombError):
        return [_REMOVED_LIMIT]


def _encode_segment(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    if image.mode == "CMYK":
        with image.convert("RGB") as rgb:
            rgb.save(buffer, format="PNG")
    else:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def _replace_image(
    item: BinaryContent, data: bytes, media_type: str, *, identifier: str | None = None
) -> BinaryContent:
    if data == item.data and media_type == item.media_type and identifier is None:
        return item
    content_type = BinaryImage if isinstance(item, BinaryImage) else BinaryContent
    return content_type(
        data,
        media_type=media_type,
        identifier=identifier or item.identifier,
        vendor_metadata=item.vendor_metadata,
    )


def _compress_image(item: BinaryContent, configuration: ImageFilterConfiguration) -> BinaryContent | str:
    raw_limit = (configuration.max_image_bytes // 4) * 3 if configuration.max_image_bytes else None
    with Image.open(io.BytesIO(item.data)) as source:
        oversized_dimension = (
            configuration.max_image_dimension > 0 and max(source.size) > configuration.max_image_dimension
        )
        oversized_bytes = raw_limit is not None and len(item.data) > raw_limit
        if not oversized_bytes and not oversized_dimension:
            return item
        if source.width * source.height > _MAX_PROCESSING_PIXELS:
            return _REMOVED_LIMIT
        if oversized_dimension:
            source.thumbnail(
                (configuration.max_image_dimension, configuration.max_image_dimension), Image.Resampling.LANCZOS
            )
        if source.mode in ("RGBA", "LA", "PA") or (source.mode == "P" and "transparency" in source.info):
            with source.convert("RGBA") as rgba:
                image = Image.new("RGB", source.size, "white")
                with rgba.getchannel("A") as alpha:
                    image.paste(rgba, mask=alpha)
        else:
            image = source.convert("RGB")
    try:
        for resize_pass in range(6):
            qualities = (20,) if resize_pass == 5 else (95, 85, 75, 60, 45, 30, 20)
            for quality in qualities:
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality, optimize=True)
                data = buffer.getvalue()
                if raw_limit is None or len(data) <= raw_limit:
                    return _replace_image(item, data, "image/jpeg")
            if resize_pass < 5:
                resized = image.resize((max(1, image.width // 2), max(1, image.height // 2)), Image.Resampling.LANCZOS)
                image.close()
                image = resized
        return _REMOVED_LIMIT
    finally:
        image.close()


__all__ = ["ImageFilterCapability", "ImageFilterConfiguration"]
