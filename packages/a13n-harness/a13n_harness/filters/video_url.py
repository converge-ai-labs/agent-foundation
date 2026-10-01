"""Request-only filtering of native video URLs for the selected model."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic_ai import VideoUrl
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart

from a13n_harness._video_urls import video_url_error

if TYPE_CHECKING:
    from a13n_harness.spec import HarnessModelCharacteristics


def project_video_urls(
    messages: list[ModelMessage], characteristics: HarnessModelCharacteristics | None
) -> list[ModelMessage] | None:
    """Replace unsupported URLs only in provider-bound input, not retained history."""
    projected = list(messages)
    changed = False
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
            elif isinstance(part.content, VideoUrl):
                items = [part.content]
            else:
                continue
            replacements: list[Any] = []
            part_changed = False
            for item in items:
                error = video_url_error(item, characteristics) if isinstance(item, VideoUrl) else None
                if error is None:
                    replacements.append(item)
                else:
                    replacements.append(
                        "<filtered-content type='video-url'>This video URL was removed because it is invalid "
                        "or unsupported by the current model.</filtered-content>"
                    )
                    part_changed = True
            if part_changed:
                content = (
                    tuple(replacements)
                    if isinstance(part.content, tuple)
                    else replacements
                    if isinstance(part.content, list)
                    else replacements[0]
                )
                parts[index] = replace(part, content=content)
                changed = message_changed = True
        if message_changed:
            projected[message_index] = replace(message, parts=parts)
    return deepcopy(projected) if changed else None
