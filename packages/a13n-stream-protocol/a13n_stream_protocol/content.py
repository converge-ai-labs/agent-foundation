"""Payload-free public tool results, after the Harness visibility boundary."""

from __future__ import annotations

import json
from typing import Any

from a13n_harness.content import ContentItem, project_input_content
from ag_ui.core import AudioPart, ContentPart, DocumentPart, FileSource, ImagePart, TextPart, UrlSource, VideoPart
from pydantic import TypeAdapter
from pydantic_ai.messages import AudioUrl, BinaryContent, DocumentUrl, ImageUrl, TextContent, UploadedFile, VideoUrl

_ANY = TypeAdapter(Any)


def tool_result_content(value: object) -> str | list[ContentPart]:
    """Project the execution value, never a lowered result's model-only supplements.

    Ordinary JSON results remain text. Explicit native content becomes ordered
    AG-UI parts; bytes are described, never embedded or materialized here.
    """
    values = value if isinstance(value, list | tuple) else [value]
    if not any(
        isinstance(
            item,
            ContentItem | BinaryContent | ImageUrl | AudioUrl | VideoUrl | DocumentUrl | UploadedFile | TextContent,
        )
        for item in values
    ):
        return _text(value)
    parts: list[ContentPart] = []
    for item in values:
        if not isinstance(
            item,
            ContentItem
            | BinaryContent
            | ImageUrl
            | AudioUrl
            | VideoUrl
            | DocumentUrl
            | UploadedFile
            | TextContent
            | str,
        ):
            parts.append(TextPart(text=_text(item)))
            continue
        projection = project_input_content(item)
        if projection is None:
            continue
        content, metadata = projection
        if not metadata.display:
            continue
        if isinstance(content, str):
            parts.append(TextPart(text=content))
            continue
        mime = content.get("media_type")
        mime = mime if isinstance(mime, str) else None
        url, file_id = content.get("url"), content.get("file_id")
        if isinstance(url, str):
            source = UrlSource(value=url, mime_type=mime)
        elif isinstance(file_id, str):
            provider = content.get("provider_name")
            source = FileSource(value=file_id, provider=provider if isinstance(provider, str) else None, mime_type=mime)
        else:
            parts.append(TextPart(text=_text(content)))
            continue
        kind = content.get("kind")
        if kind == "image-url" or (mime or "").startswith("image/"):
            parts.append(ImagePart(source=source))
        elif kind == "audio-url" or (mime or "").startswith("audio/"):
            parts.append(AudioPart(source=source))
        elif kind == "video-url" or (mime or "").startswith("video/"):
            parts.append(VideoPart(source=source))
        else:
            parts.append(DocumentPart(source=source))
    return parts


def public_tool_value(value: object) -> object:
    if isinstance(value, bytes | bytearray):
        return {"payload_omitted": True, "size_bytes": len(value)}
    if isinstance(
        value, ContentItem | BinaryContent | ImageUrl | AudioUrl | VideoUrl | DocumentUrl | UploadedFile | TextContent
    ):
        projected = project_input_content(value)
        return projected[0] if projected is not None and projected[1].display else {"payload_omitted": True}
    if isinstance(value, dict):
        return {key: public_tool_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [public_tool_value(item) for item in value]
    return value


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        _ANY.dump_python(public_tool_value(value), mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
