"""Request-only video compatibility and Base64 byte-budget projection."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic_ai import BinaryContent, VideoUrl
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart

from a13n_harness._video_urls import video_url_error
from a13n_harness.video_input import VideoInputPolicy, encoded_video_bytes

if TYPE_CHECKING:
    from a13n_harness.spec import HarnessModelCharacteristics


def project_video_urls(
    messages: list[ModelMessage], characteristics: HarnessModelCharacteristics | None
) -> list[ModelMessage] | None:
    """Bound provider-bound videos without modifying retained history or bytes."""
    from a13n_harness.spec import ModelCapability

    limits = characteristics.video_input if characteristics else VideoInputPolicy()
    binary_supported = (
        characteristics is not None and ModelCapability.VIDEO_UNDERSTANDING in characteristics.capabilities
    )
    projected = list(messages)
    detach = False
    total = 0
    for message_index, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            continue
        parts = list(message.parts)
        message_changed = False
        for index, part in enumerate(parts):
            if not isinstance(part, UserPromptPart) and type(part) is not ToolReturnPart:
                continue
            if isinstance(part.content, (list, tuple)):
                items = list(part.content)
            elif isinstance(part.content, (VideoUrl, BinaryContent)):
                items = [part.content]
            else:
                continue
            replacements: list[Any] = []
            part_changed = False
            for item in items:
                error = None
                if isinstance(item, VideoUrl):
                    error = video_url_error(item, characteristics)
                elif isinstance(item, BinaryContent) and item.media_type.startswith("video/"):
                    # Exact-error healing mutates its input. Detach even unchanged
                    # inline videos; deepcopy shares immutable bytes, not envelopes.
                    detach = True
                    size = encoded_video_bytes(len(item.data))
                    if not binary_supported:
                        error = "video_input_unsupported"
                    elif total + size > limits.max_video_bytes:
                        error = "video_input_too_large"
                    else:
                        total += size
                if error is None:
                    replacements.append(item)
                else:
                    reason = (
                        "Use read_video_url to acquire this direct URL within the video byte budget."
                        if error == "video_url_requires_materialization"
                        else "This video is invalid, unsupported, or exceeds the request's Base64 video byte budget."
                    )
                    replacements.append(f"<filtered-content type='video' reason='{error}'>{reason}</filtered-content>")
                    detach = part_changed = True
            if part_changed:
                content = (
                    tuple(replacements)
                    if isinstance(part.content, tuple)
                    else replacements
                    if isinstance(part.content, list)
                    else replacements[0]
                )
                parts[index] = replace(part, content=content)
                message_changed = True
        if message_changed:
            projected[message_index] = replace(message, parts=parts)
    return deepcopy(projected) if detach else None
