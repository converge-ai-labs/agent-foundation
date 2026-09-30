"""Harness-owned presentation annotations around native content values.

Annotations belong to canonical requests, not provider parameters. Structural
transforms carry content and annotations together; provider-normalized temporary
histories are never used to reconstruct application annotations.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai import _agent_graph
from pydantic_ai.messages import (
    BinaryContent,
    CachePoint,
    FileUrl,
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
    TextContent,
    ToolReturnPart,
    UploadedFile,
    UserContent,
    UserPromptPart,
)

CONTENT_METADATA_KEY = "a13n.content"


class ContentMetadata(BaseModel):
    """Presentation and opaque Host references; never instructions or authority."""

    model_config = ConfigDict(frozen=True, extra="allow")
    display: bool = True
    source_id: str | None = None
    media: bool = False

    @classmethod
    def from_native(cls, metadata: object) -> ContentMetadata:
        return cls.model_validate(metadata) if isinstance(metadata, dict) else cls()


@dataclass(frozen=True, slots=True)
class ContentItem:
    """One native value and its application annotations at a Harness boundary."""

    value: UserContent
    metadata: ContentMetadata = field(default_factory=ContentMetadata)


def content_items(content: str | Sequence[UserContent | ContentItem]) -> list[ContentItem]:
    items = [content] if isinstance(content, str) else content
    return [
        item
        if isinstance(item, ContentItem)
        else ContentItem(
            item, ContentMetadata.from_native(item.metadata) if isinstance(item, TextContent) else ContentMetadata()
        )
        for item in items
    ]


def project_input_content(item: UserContent | ContentItem) -> tuple[str | dict[str, JsonValue], ContentMetadata] | None:
    """Describe an input without transporting native media payloads."""
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
            pass  # Native URLs need not carry a MIME hint.
    elif isinstance(item, UploadedFile):
        media.update(file_id=item.file_id, provider_name=item.provider_name, media_type=item.media_type)
    return media, metadata


def native_content(content: str | Sequence[UserContent | ContentItem]) -> str | list[UserContent]:
    if isinstance(content, str):
        return content
    return [item.value for item in content_items(content)]


def prompt_content(request: ModelRequest, part_index: int) -> list[ContentItem]:
    part = request.parts[part_index]
    assert isinstance(part, UserPromptPart)
    items = content_items(part.content)
    annotations = (request.metadata or {}).get(CONTENT_METADATA_KEY, {})
    entries = annotations.get(str(part_index)) if isinstance(annotations, dict) else None
    if entries is None:
        return items
    if not isinstance(entries, list) or len(entries) != len(items):
        raise ValueError("Content annotations do not match their canonical prompt part")
    return [
        ContentItem(item.value, ContentMetadata.model_validate(metadata))
        for item, metadata in zip(items, entries, strict=True)
    ]


def request_input_content(request: ModelRequest) -> list[ContentItem]:
    """Shared live/history projection of canonical request annotations."""
    return [
        item
        for index, part in enumerate(request.parts)
        if isinstance(part, UserPromptPart)
        for item in prompt_content(request, index)
    ]


def annotate_prompt(request: ModelRequest, part_index: int, items: Sequence[ContentItem]) -> ModelRequest:
    annotations = dict((request.metadata or {}).get(CONTENT_METADATA_KEY, {}))
    annotations[str(part_index)] = [item.metadata.model_dump(mode="json") for item in items]
    return replace(request, metadata={**(request.metadata or {}), CONTENT_METADATA_KEY: annotations})


def input_request(
    content: str | Sequence[UserContent | ContentItem], *, metadata: dict[str, Any] | None = None
) -> ModelRequest:
    items = content_items(content)
    request = ModelRequest(parts=[UserPromptPart(native_content(content))], metadata=metadata)
    return annotate_prompt(request, 0, items)


def replace_request_parts(
    request: ModelRequest, parts: Sequence[tuple[ModelRequestPart, list[ContentItem] | None]]
) -> ModelRequest:
    """Rebuild annotations together with an insertion, reorder, or merge of parts."""
    metadata = {key: value for key, value in (request.metadata or {}).items() if key != CONTENT_METADATA_KEY}
    result = replace(request, parts=tuple(part for part, _ in parts), metadata=metadata or None)
    for index, (_, items) in enumerate(parts):
        if items is not None:
            result = annotate_prompt(result, index, items)
    return result


def request_parts(request: ModelRequest) -> list[tuple[ModelRequestPart, list[ContentItem] | None]]:
    return [
        (part, prompt_content(request, index) if isinstance(part, UserPromptPart) else None)
        for index, part in enumerate(request.parts)
    ]


def _repair_request_history(
    messages: Sequence[ModelMessage], *, has_new_prompt: bool, has_deferred_results: bool
) -> list[ModelMessage]:
    """Adapt native repair's request-local structural edits, not its pairing rules.

    Drop/repair never removes, inserts, or reorders UserPromptPart values and
    preserves each existing request's metadata. Carry an ordinal annotation
    ledger through these two passes, then rebuild paired canonical slots before
    merging. This local ledger is neither persisted nor sent to a provider.
    Dependency regressions pin this narrow use of the native repair helpers.
    """
    prepared = [
        replace(
            message,
            metadata={
                **(message.metadata or {}),
                CONTENT_METADATA_KEY: [
                    (message.metadata or {}).get(CONTENT_METADATA_KEY, {}).get(str(index))
                    for index, part in enumerate(message.parts)
                    if isinstance(part, UserPromptPart)
                ],
            },
        )
        if isinstance(message, ModelRequest)
        else message
        for message in messages
    ]
    repaired = _agent_graph._drop_orphaned_tool_results(prepared)  # pyright: ignore[reportPrivateUsage]
    repaired = _agent_graph._repair_dangling_tool_calls(repaired)  # pyright: ignore[reportPrivateUsage]
    if not has_deferred_results:
        repaired = _agent_graph._repair_interrupted_tail(  # pyright: ignore[reportPrivateUsage]
            repaired, has_new_prompt=has_new_prompt
        )
    result: list[ModelMessage] = []
    for message in repaired:
        if not isinstance(message, ModelRequest):
            result.append(message)
            continue
        ledger = iter((message.metadata or {}).get(CONTENT_METADATA_KEY, []))
        annotations = {}
        for index, part in enumerate(message.parts):
            if isinstance(part, UserPromptPart):
                entries = next(ledger)
                if entries is not None:
                    annotations[str(index)] = entries
        if list(ledger):
            raise ValueError("Native history repair changed the prompt annotation ledger")
        metadata = {key: value for key, value in (message.metadata or {}).items() if key != CONTENT_METADATA_KEY}
        if annotations:
            metadata[CONTENT_METADATA_KEY] = annotations
        result.append(replace(message, metadata=metadata or None))
    return result


def normalize_request_history(
    messages: Sequence[ModelMessage], *, has_new_prompt: bool = False, has_deferred_results: bool = False
) -> tuple[ModelMessage, ...]:
    """Repair and merge canonical requests jointly with their annotations.

    Native outbound rendering may independently normalize temporary history;
    its initialization must not shift or discard canonical annotation slots.
    """
    result: list[ModelMessage] = []
    for message in _repair_request_history(
        messages, has_new_prompt=has_new_prompt, has_deferred_results=has_deferred_results
    ):
        previous = result[-1] if result else None
        if (
            isinstance(message, ModelRequest)
            and isinstance(previous, ModelRequest)
            and (not previous.instructions or not message.instructions or previous.instructions == message.instructions)
        ):
            parts = request_parts(previous) + request_parts(message)
            parts.sort(key=lambda item: 0 if isinstance(item[0], ToolReturnPart | RetryPromptPart) else 1)
            metadata = {**(previous.metadata or {}), **(message.metadata or {})}
            input_ids = [
                input_id
                for request in (previous, message)
                for value in [(request.metadata or {}).get("a13n.steering-input")]
                for input_id in (value if isinstance(value, list) else [value])
                if isinstance(input_id, str)
            ]
            if input_ids:
                metadata["a13n.steering-input"] = input_ids
            previous_framework = (previous.metadata or {}).get("__pydantic_ai__")
            current_framework = (message.metadata or {}).get("__pydantic_ai__")
            if isinstance(previous_framework, dict) and isinstance(current_framework, dict):
                metadata["__pydantic_ai__"] = {**previous_framework, **current_framework}
            merged = replace(message, instructions=previous.instructions or message.instructions, metadata=metadata)
            result[-1] = replace_request_parts(merged, parts)
        elif (
            isinstance(message, ModelResponse)
            and isinstance(previous, ModelResponse)
            and all(
                response.provider_response_id is None and response.provider_name is None and response.model_name is None
                for response in (previous, message)
            )
        ):
            result[-1] = replace(previous, parts=[*previous.parts, *message.parts])
        else:
            result.append(message)
    return tuple(result)
