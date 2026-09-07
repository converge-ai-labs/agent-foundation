"""Shared typed progressive-disclosure helpers for model-facing Toolsets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Literal, TypedDict, cast

from pydantic import JsonValue, TypeAdapter

from a13n_harness._json import dump_json_bytes, dump_json_text, redact_json
from a13n_harness.context import AgentContext
from a13n_harness.tools._output import (
    DEFAULT_TOOL_OUTPUT_CHARS,
    FINAL_TOOL_OUTPUT_HARD_CHARS,
    acknowledge_tool_output,
)

MAX_TOOL_OUTPUT_SPILL_BYTES = 16 * 1024 * 1024

_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])


class ToolOutputDisclosure(TypedDict):
    """Truthful recovery metadata for one proactively bounded Toolset result."""

    truncated: Literal[True]
    output_chars: int
    output_bytes: int
    output_file_path: str | None
    content_complete: bool
    hint: str


def tool_output_text(value: Mapping[str, JsonValue]) -> str:
    """Return the strict compact JSON text representation of one result."""
    projected = _JSON_OBJECT_ADAPTER.validate_python(dict(value), strict=True)
    return dump_json_text(projected)


def tool_output_bytes(value: Mapping[str, JsonValue]) -> bytes:
    """Return the strict compact JSON result encoded as UTF-8 bytes."""
    return tool_output_text(value).encode("utf-8")


def tool_output_size(value: Mapping[str, JsonValue]) -> int:
    """Measure one result in serialized JSON characters."""
    return len(tool_output_text(value))


async def create_tool_output_disclosure(
    context: AgentContext,
    value: Mapping[str, JsonValue],
    *,
    content_complete: bool,
    noun: str = "output",
    max_spill_bytes: int = MAX_TOOL_OUTPUT_SPILL_BYTES,
) -> ToolOutputDisclosure:
    """Best-effort save the fullest available redacted JSON value and describe it."""
    if max_spill_bytes <= 0:
        raise ValueError("max_spill_bytes must be positive")
    safe_value = _redacted_mapping(value)
    encoded = dump_json_bytes(safe_value)
    output_file_path = (
        await context._spill_tool_result(encoded, suffix=".json") if len(encoded) <= max_spill_bytes else None
    )
    completeness = "complete" if content_complete else "fullest available"
    if output_file_path is not None:
        hint = f"The {completeness} {noun} is saved at output_file_path. Use view to inspect it."
    else:
        hint = f"The {completeness} {noun} could not be saved. Use continuation parameters or narrow the request."
    return {
        "truncated": True,
        "output_chars": len(encoded.decode("utf-8")),
        "output_bytes": len(encoded),
        "output_file_path": output_file_path,
        "content_complete": content_complete,
        "hint": hint,
    }


def continuation_disclosure(
    value: Mapping[str, JsonValue],
    *,
    hint: str,
) -> ToolOutputDisclosure:
    """Describe an inline page whose remaining content has a reliable continuation."""
    safe_value = _redacted_mapping(value)
    return {
        "truncated": True,
        "output_chars": tool_output_size(safe_value),
        "output_bytes": len(tool_output_bytes(safe_value)),
        "output_file_path": None,
        "content_complete": False,
        "hint": hint,
    }


async def disclose_text_fields(
    context: AgentContext,
    value: Mapping[str, JsonValue],
    *,
    text_fields: Sequence[str],
    content_complete: bool,
    noun: str = "output",
    limit: int = DEFAULT_TOOL_OUTPUT_CHARS,
    preserve_tail: bool = False,
) -> dict[str, JsonValue]:
    """Fit selected text fields, spilling the fullest available value when needed."""
    result = _redacted_mapping(value)
    if tool_output_size(result) <= limit:
        return acknowledge_tool_output(result)
    disclosure = await create_tool_output_disclosure(
        context,
        result,
        content_complete=content_complete,
        noun=noun,
    )
    preview: dict[str, JsonValue] = {**result, "disclosure": cast(JsonValue, disclosure)}
    suffix = "\n[... output truncated; see disclosure ...]\n"
    preview = fit_text_fields_to_limit(
        preview,
        text_fields=text_fields,
        limit=limit,
        suffix=suffix,
        preserve_tail=preserve_tail,
    )
    return acknowledge_tool_output(_minimal_if_oversized(preview, disclosure=disclosure, limit=limit))


async def disclose_text_paths(
    context: AgentContext,
    value: Mapping[str, JsonValue],
    *,
    text_paths: Sequence[tuple[str, ...]],
    content_complete: bool,
    noun: str = "output",
    limit: int = DEFAULT_TOOL_OUTPUT_CHARS,
    preserve_tail: bool = False,
) -> dict[str, JsonValue]:
    """Fit nested semantic text fields while preserving the surrounding result shape."""
    result = deepcopy(_redacted_mapping(value))
    if tool_output_size(result) <= limit:
        return acknowledge_tool_output(result)
    disclosure = await create_tool_output_disclosure(
        context,
        result,
        content_complete=content_complete,
        noun=noun,
    )
    result["disclosure"] = cast(JsonValue, disclosure)
    originals: dict[tuple[str, ...], str] = {}
    for path in text_paths:
        item = _path_value(result, path)
        if isinstance(item, str):
            originals[path] = item
            _set_path_value(result, path, "")
    if originals:
        available = max(0, limit - tool_output_size(result))
        per_field = available // len(originals)
        suffix = "\n[... output truncated; see disclosure ...]\n"
        while True:
            for path, text in originals.items():
                _set_path_value(
                    result,
                    path,
                    _truncate_text(text, per_field, suffix=suffix, preserve_tail=preserve_tail),
                )
            if tool_output_size(result) <= limit or per_field <= 0:
                break
            per_field = max(0, int(per_field * 0.8) - 1)
    return acknowledge_tool_output(_minimal_if_oversized(result, disclosure=disclosure, limit=limit))


async def disclose_sequence_field(
    context: AgentContext,
    value: Mapping[str, JsonValue],
    *,
    field: str,
    content_complete: bool,
    noun: str = "output",
    limit: int = DEFAULT_TOOL_OUTPUT_CHARS,
    continuation_hint: str | None = None,
) -> tuple[dict[str, JsonValue], int]:
    """Keep as many complete sequence items as fit and spill the fuller value."""
    result = _redacted_mapping(value)
    items = result.get(field)
    if not isinstance(items, list):
        raise TypeError(f"{field} must be a list")
    if tool_output_size(result) <= limit:
        if content_complete or continuation_hint is None:
            return acknowledge_tool_output(result), len(items)
        continuation = continuation_disclosure(result, hint=continuation_hint)
        continued = {**result, "disclosure": cast(JsonValue, continuation)}
        if tool_output_size(continued) <= limit:
            return acknowledge_tool_output(continued), len(items)
    disclosure = await create_tool_output_disclosure(
        context,
        result,
        content_complete=content_complete,
        noun=noun,
    )
    preview: dict[str, JsonValue] = {
        **result,
        field: [],
        "disclosure": cast(JsonValue, disclosure),
    }
    shown = 0
    selected = cast(list[JsonValue], preview[field])
    for item in items:
        selected.append(item)
        if tool_output_size(preview) > limit:
            selected.pop()
            break
        shown += 1
    bounded = _minimal_if_oversized(preview, disclosure=disclosure, limit=limit)
    return acknowledge_tool_output(bounded), shown


async def disclose_mapping_field(
    context: AgentContext,
    value: Mapping[str, JsonValue],
    *,
    field: str,
    content_complete: bool,
    noun: str = "output",
    limit: int = DEFAULT_TOOL_OUTPUT_CHARS,
    continuation_hint: str | None = None,
) -> tuple[dict[str, JsonValue], int]:
    """Keep as many complete mapping entries as fit and spill the fuller value."""
    result = _redacted_mapping(value)
    items = result.get(field)
    if not isinstance(items, dict):
        raise TypeError(f"{field} must be a mapping")
    if tool_output_size(result) <= limit:
        if content_complete or continuation_hint is None:
            return acknowledge_tool_output(result), len(items)
        continuation = continuation_disclosure(result, hint=continuation_hint)
        continued = {**result, "disclosure": cast(JsonValue, continuation)}
        if tool_output_size(continued) <= limit:
            return acknowledge_tool_output(continued), len(items)
    disclosure = await create_tool_output_disclosure(
        context,
        result,
        content_complete=content_complete,
        noun=noun,
    )
    preview: dict[str, JsonValue] = {
        **result,
        field: {},
        "disclosure": cast(JsonValue, disclosure),
    }
    shown = 0
    selected = cast(dict[str, JsonValue], preview[field])
    for key, item in items.items():
        selected[key] = item
        if tool_output_size(preview) > limit:
            selected.pop(key)
            break
        shown += 1
    bounded = _minimal_if_oversized(preview, disclosure=disclosure, limit=limit)
    return acknowledge_tool_output(bounded), shown


def fit_text_fields_to_limit(
    value: Mapping[str, JsonValue],
    *,
    text_fields: Sequence[str],
    limit: int,
    suffix: str,
    preserve_tail: bool = False,
) -> dict[str, JsonValue]:
    """Shrink selected string fields until the complete serialized result fits."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    preview = dict(value)
    if tool_output_size(preview) <= limit:
        return preview
    originals = {field: item for field in text_fields if isinstance((item := preview.get(field)), str)}
    if not originals:
        return preview
    for field in originals:
        preview[field] = ""
    available = max(0, limit - tool_output_size(preview))
    per_field = available // len(originals)
    while True:
        for field, text in originals.items():
            preview[field] = _truncate_text(
                text,
                per_field,
                suffix=suffix,
                preserve_tail=preserve_tail,
            )
        if tool_output_size(preview) <= limit or per_field <= 0:
            return preview
        per_field = max(0, int(per_field * 0.8) - 1)


def _redacted_mapping(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    projected = _JSON_OBJECT_ADAPTER.validate_python(dict(value), strict=True)
    safe_value = redact_json(projected)
    assert isinstance(safe_value, dict)
    return safe_value


def _path_value(value: Mapping[str, JsonValue], path: tuple[str, ...]) -> JsonValue | None:
    current: JsonValue = cast(JsonValue, value)
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _set_path_value(value: dict[str, JsonValue], path: tuple[str, ...], item: JsonValue) -> None:
    if not path:
        raise ValueError("text path cannot be empty")
    current = value
    for part in path[:-1]:
        selected = current.get(part)
        if not isinstance(selected, dict):
            raise ValueError("text path does not select a mapping")
        current = selected
    current[path[-1]] = item


def _minimal_if_oversized(
    value: dict[str, JsonValue],
    *,
    disclosure: ToolOutputDisclosure,
    limit: int,
) -> dict[str, JsonValue]:
    if tool_output_size(value) <= limit:
        return value
    minimal: dict[str, JsonValue] = {
        "ok": value.get("ok", True),
        "disclosure": cast(JsonValue, disclosure),
    }
    if tool_output_size(minimal) > limit:
        raise ValueError("tool output limit cannot contain disclosure metadata")
    return minimal


def _truncate_text(
    value: str,
    limit: int,
    *,
    suffix: str,
    preserve_tail: bool,
) -> str:
    if len(value) <= limit:
        return value
    if len(suffix) >= limit:
        return suffix[: max(0, limit)]
    available = limit - len(suffix)
    if not preserve_tail:
        return f"{value[:available]}{suffix}"
    head_size = available // 2
    tail_size = available - head_size
    tail = value[-tail_size:] if tail_size else ""
    return f"{value[:head_size]}{suffix}{tail}"


__all__ = [
    "DEFAULT_TOOL_OUTPUT_CHARS",
    "FINAL_TOOL_OUTPUT_HARD_CHARS",
    "MAX_TOOL_OUTPUT_SPILL_BYTES",
    "ToolOutputDisclosure",
    "acknowledge_tool_output",
    "continuation_disclosure",
    "create_tool_output_disclosure",
    "disclose_mapping_field",
    "disclose_sequence_field",
    "disclose_text_fields",
    "disclose_text_paths",
    "fit_text_fields_to_limit",
    "tool_output_bytes",
    "tool_output_size",
    "tool_output_text",
]
