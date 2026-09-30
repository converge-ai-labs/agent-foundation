"""Definition-selected compatibility filtering for native multimodal request content."""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    ImageUrl,
    RunContext,
    TextContent,
    UploadedFile,
    VideoUrl,
)
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext

from a13n_harness._urls import require_audience_safe_url
from a13n_harness.context import AgentContext

CONTENT_FILTER_CAPABILITY_ID = "a13n.filter.content"

type MediaFamily = Literal["image", "audio", "video", "document", "uploaded_file"]

_ALL_MEDIA: frozenset[MediaFamily] = frozenset({"image", "audio", "video", "document", "uploaded_file"})


class ContentFilterConfiguration(BaseModel):
    """Finite native-media compatibility policy for one Agent definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted_media: frozenset[MediaFamily] = _ALL_MEDIA
    max_media_items: int = Field(default=64, ge=0, le=1_024)
    max_binary_bytes: int = Field(default=64 * 1024 * 1024, ge=0, le=512 * 1024 * 1024)


@dataclass(init=False)
class ContentFilterCapability(AbstractCapability[AgentContext]):
    """Replace unsupported or unsafe request media before native model rendering."""

    id = CONTENT_FILTER_CAPABILITY_ID

    def __init__(self, configuration: ContentFilterConfiguration | None = None) -> None:
        if configuration is None:
            configuration = ContentFilterConfiguration()
        elif not isinstance(configuration, ContentFilterConfiguration):
            configuration = ContentFilterConfiguration.model_validate(configuration, strict=True)
        self.configuration = configuration.model_copy(deep=True)

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        del ctx
        messages = list(request_context.messages)
        media_items = 0
        binary_bytes = 0
        changed = False

        for message_index, message in enumerate(messages):
            if not isinstance(message, ModelRequest):
                continue
            parts = list(message.parts)
            message_changed = False
            for part_index, part in enumerate(parts):
                if not isinstance(part, UserPromptPart) and type(part) is not ToolReturnPart:
                    continue
                content = part.content
                if isinstance(content, str | bytes | bytearray | memoryview):
                    continue
                if _is_media(content):
                    items = [content]
                    shape: Literal["scalar", "list", "tuple"] = "scalar"
                elif isinstance(content, list):
                    items = list(content)
                    shape = "list"
                elif isinstance(content, tuple):
                    items = list(content)
                    shape = "tuple"
                else:
                    continue

                filtered: list[Any] = []
                part_changed = False
                for item in items:
                    family = _media_family(item)
                    if family is None:
                        filtered.append(item)
                        continue
                    assert isinstance(item, BinaryContent | ImageUrl | AudioUrl | VideoUrl | DocumentUrl | UploadedFile)
                    item_bytes = len(item.data) if isinstance(item, BinaryContent) else 0
                    unsafe_url = _has_unsafe_url(item)
                    if (
                        unsafe_url
                        or family not in self.configuration.accepted_media
                        or media_items >= self.configuration.max_media_items
                        or binary_bytes + item_bytes > self.configuration.max_binary_bytes
                    ):
                        text = _filtered_message(family, unsafe=unsafe_url)
                        filtered.append(
                            TextContent(text, metadata=deepcopy(item.vendor_metadata))
                            if isinstance(part, UserPromptPart)
                            else text
                        )
                        part_changed = True
                        continue
                    media_items += 1
                    binary_bytes += item_bytes
                    filtered.append(item)

                if not part_changed:
                    continue
                changed = message_changed = True
                if shape == "scalar" and len(filtered) == 1:
                    replacement = filtered[0]
                elif shape == "tuple":
                    replacement = tuple(filtered)
                else:
                    replacement = filtered
                parts[part_index] = replace(part, content=replacement)
            if message_changed:
                messages[message_index] = replace(message, parts=parts)

        if not changed:
            return request_context
        updated = copy(request_context)
        updated.messages = deepcopy(messages)
        return updated


def _is_media(value: object) -> bool:
    return isinstance(
        value,
        BinaryContent | ImageUrl | AudioUrl | VideoUrl | DocumentUrl | UploadedFile,
    )


def _media_family(value: object) -> MediaFamily | None:
    if isinstance(value, ImageUrl):
        return "image"
    if isinstance(value, AudioUrl):
        return "audio"
    if isinstance(value, VideoUrl):
        return "video"
    if isinstance(value, DocumentUrl):
        return "document"
    if isinstance(value, UploadedFile):
        return "uploaded_file"
    if not isinstance(value, BinaryContent):
        return None
    media_type = value.media_type.partition(";")[0].strip().casefold()
    if media_type.startswith("image/"):
        return "image"
    if media_type.startswith("audio/"):
        return "audio"
    if media_type.startswith("video/"):
        return "video"
    return "document"


def _has_unsafe_url(value: object) -> bool:
    if not isinstance(value, ImageUrl | AudioUrl | VideoUrl | DocumentUrl):
        return False
    try:
        require_audience_safe_url(value.url)
    except (TypeError, ValueError):
        return True
    return False


def _filtered_message(family: MediaFamily, *, unsafe: bool) -> str:
    reason = "its URL is unsafe for model context" if unsafe else "it is unsupported or exceeds request limits"
    return f"<filtered-content type='{family}'>This content was removed because {reason}.</filtered-content>"


__all__ = [
    "CONTENT_FILTER_CAPABILITY_ID",
    "ContentFilterCapability",
    "ContentFilterConfiguration",
    "MediaFamily",
]
