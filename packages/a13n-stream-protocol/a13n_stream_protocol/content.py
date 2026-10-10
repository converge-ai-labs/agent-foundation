"""Payload-free public tool results, after the Harness visibility boundary."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass

from a13n_harness._json import project_json
from a13n_harness.content import ContentItem, project_input_content
from ag_ui.core import AudioPart, ContentPart, DocumentPart, FileSource, ImagePart, TextPart, UrlSource, VideoPart
from pydantic import BaseModel
from pydantic_ai.messages import AudioUrl, BinaryContent, DocumentUrl, ImageUrl, TextContent, UploadedFile, VideoUrl

_NATIVE_CONTENT = (
    ContentItem | BinaryContent | ImageUrl | AudioUrl | VideoUrl | DocumentUrl | UploadedFile | TextContent
)


def tool_result_content(value: object) -> str | list[ContentPart]:
    """Project the execution value, never a lowered result's model-only supplements.

    Ordinary JSON results remain text. Explicit native content becomes ordered
    AG-UI parts; bytes are described, never embedded or materialized here.
    """
    values = value if isinstance(value, list | tuple) else [value]
    if not any(isinstance(item, _NATIVE_CONTENT) for item in values):
        return _text(value)
    parts: list[ContentPart] = []
    for item in values:
        if not isinstance(item, _NATIVE_CONTENT | str):
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
    if isinstance(value, _NATIVE_CONTENT):
        projected = project_input_content(value)
        return projected[0] if projected is not None and projected[1].display else {"payload_omitted": True}
    if isinstance(value, dict):
        return {key: public_tool_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [public_tool_value(item) for item in value]
    if isinstance(value, BaseModel) or (is_dataclass(value) and not isinstance(value, type)):
        # A custom serializer can flatten or rename media fields, so filtering
        # its JSON output is too late. Keep media-bearing records payload-free.
        if _contains_native_content(value, set()):
            return {"payload_omitted": True}
        return project_json(value)
    return value


def _contains_native_content(value: object, seen: set[int]) -> bool:
    if isinstance(value, bytes | bytearray | _NATIVE_CONTENT):
        return True
    if id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, BaseModel):
        children = [getattr(value, name) for name in type(value).model_fields]
        children.extend((value.model_extra or {}).values())
    elif is_dataclass(value) and not isinstance(value, type):
        children = [getattr(value, field.name) for field in fields(value)]
    elif isinstance(value, dict):
        children = value.values()
    elif isinstance(value, list | tuple):
        children = value
    else:
        return False
    return any(_contains_native_content(child, seen) for child in children)


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        project_json(public_tool_value(value)),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
