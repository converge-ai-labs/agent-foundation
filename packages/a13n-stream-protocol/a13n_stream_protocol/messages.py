"""Payload-free native input projection shared by protocol consumers."""

from __future__ import annotations

from a13n_harness.content import ContentItem
from a13n_harness.content import ContentMetadata as ContentMetadata
from pydantic import JsonValue
from pydantic_ai.messages import BinaryContent, CachePoint, FileUrl, TextContent, UploadedFile, UserContent


def project_input_content(item: UserContent | ContentItem) -> tuple[str | dict[str, JsonValue], ContentMetadata] | None:
    """Keep native media references and metadata, without serializing payloads.

    The native models stay in the Harness event. This is a one-way observation
    projection, not a codec for restoring model input or a media storage service.
    """
    metadata = ContentMetadata()
    if isinstance(item, ContentItem):
        metadata = item.metadata
        item = item.value
    elif isinstance(item, TextContent):
        metadata = ContentMetadata.from_native(item.metadata)
    if isinstance(item, str):
        return item, metadata
    if isinstance(item, TextContent):
        return item.content, metadata
    if isinstance(item, CachePoint):
        return None
    metadata = metadata.model_copy(update={"media": True})
    media: dict[str, JsonValue] = {"kind": item.kind}
    if isinstance(item, BinaryContent):
        media.update(media_type=item.media_type, size_bytes=len(item.data), payload_omitted=True)
    elif isinstance(item, FileUrl):
        if item.url.startswith(("https://", "http://")):
            media["url"] = item.url
        else:
            media["payload_omitted"] = True
        try:
            media["media_type"] = item.media_type
        except ValueError:
            pass  # A native URL need not have a filename extension or MIME hint.
    elif isinstance(item, UploadedFile):
        media.update(file_id=item.file_id, provider_name=item.provider_name, media_type=item.media_type)
    return media, metadata
