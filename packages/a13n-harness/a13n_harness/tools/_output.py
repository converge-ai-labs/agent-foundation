"""Generic tool result bounding, redaction, acknowledgement, and run-private spills."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Iterator, Mapping
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from pydantic import JsonValue, ValidationError
from pydantic_ai import TextContent, ToolReturn
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import CachePoint

from a13n_harness._json import (
    dump_json_bytes,
    dump_json_text,
    is_sensitive_key,
    redact_bearer,
    redact_json,
    require_finite_json,
)
from a13n_harness.content import content_items

if TYPE_CHECKING:
    from a13n_harness.context import AgentContext
    from a13n_harness.environment.providers import FileScopeSelection
    from a13n_harness.tools.metadata import ToolOutputPolicy

DEFAULT_TOOL_OUTPUT_CHARS = 12_000
FINAL_TOOL_OUTPUT_HARD_CHARS = 20_000

# Nested CodeAct calls retain their execution value until the outer runner settles.
_NESTED_TOOL_EXECUTION: ContextVar[bool] = ContextVar("a13n_nested_tool_execution", default=False)
TOOL_CONTENT_METADATA_KEY = "a13n.tool-content"


class AcknowledgedToolOutput(dict[str, JsonValue]):
    """Ordinary JSON mapping marked as proactively bounded by its Toolset."""


def acknowledge_tool_output(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Attach the process-local acknowledgement without changing model-visible shape."""
    return cast(dict[str, JsonValue], AcknowledgedToolOutput(dict(value)))


def is_acknowledged_tool_output(value: object) -> bool:
    """Return whether a result carries the Harness-owned acknowledgement type."""
    return isinstance(value, AcknowledgedToolOutput)


async def _apply_result_policy(
    result: Any,
    policy: ToolOutputPolicy,
    *,
    context: AgentContext | None = None,
    reject_non_json: bool = True,
) -> Any:
    """Apply the textual result boundary without rewriting native media."""
    if is_acknowledged_tool_output(result):
        return await _apply_acknowledged_json_result_policy(result, policy)
    if isinstance(result, ToolReturn):
        return await _apply_native_tool_return_policy(
            result,
            policy,
            context=context,
            reject_non_json=reject_non_json,
        )
    if not reject_non_json and not _is_native_json_result(result):
        return result
    return await _apply_json_result_policy(result, policy, context=context)


async def _apply_acknowledged_json_result_policy(
    result: Any,
    policy: ToolOutputPolicy,
) -> JsonValue:
    """Keep semantic Toolset output intact unless it violates the larger hard ceiling."""
    try:
        value = _project_json_result(result)
        safe_value = redact_json(value) if policy.redact else deepcopy(value)
        output_chars, output_bytes, head, tail = _scan_json(
            safe_value,
            keep_bytes=min(policy.max_inline_bytes, FINAL_TOOL_OUTPUT_HARD_CHARS) // 2,
        )
    except (RecursionError, TypeError, ValueError, ValidationError) as exc:
        raise ToolFailed("Tool returned an invalid result.") from exc
    if output_chars <= FINAL_TOOL_OUTPUT_HARD_CHARS and output_bytes <= policy.max_output_bytes:
        return safe_value
    return _bounded_json_preview(
        safe_value,
        output_chars=output_chars,
        output_bytes=output_bytes,
        output_file_path=None,
        head=head,
        tail=tail,
        char_limit=FINAL_TOOL_OUTPUT_HARD_CHARS,
        byte_limit=policy.max_inline_bytes,
    )


async def _apply_json_result_policy(
    result: Any,
    policy: ToolOutputPolicy,
    *,
    context: AgentContext | None,
) -> JsonValue:
    try:
        value = _project_json_result(result)
        safe_value = redact_json(value) if policy.redact else deepcopy(value)
        output_chars, output_bytes, head, tail = _scan_json(
            safe_value,
            keep_bytes=min(policy.max_inline_bytes, FINAL_TOOL_OUTPUT_HARD_CHARS) // 2,
        )
    except (RecursionError, TypeError, ValueError, ValidationError) as exc:
        raise ToolFailed("Tool returned an invalid result.") from exc

    if output_chars <= FINAL_TOOL_OUTPUT_HARD_CHARS and output_bytes <= policy.max_inline_bytes:
        return safe_value
    if policy.overflow == "fail":
        raise ToolFailed("Tool result exceeded its output limit.")

    output_file_path: str | None = None
    if policy.overflow == "spill" and output_bytes <= policy.max_output_bytes and context is not None:
        encoded = dump_json_bytes(safe_value)
        output_file_path = await context._spill_tool_result(encoded, suffix=".json")

    return _bounded_json_preview(
        safe_value,
        output_chars=output_chars,
        output_bytes=output_bytes,
        output_file_path=output_file_path,
        head=head,
        tail=tail,
        char_limit=FINAL_TOOL_OUTPUT_HARD_CHARS,
        byte_limit=policy.max_inline_bytes,
    )


async def _apply_native_tool_return_policy(
    result: ToolReturn,
    policy: ToolOutputPolicy,
    *,
    context: AgentContext | None,
    reject_non_json: bool,
) -> ToolReturn:
    """Bound textual fields while preserving every native multimodal value."""
    try:
        return_value = await _apply_optional_json_result_policy(
            result.return_value,
            policy,
            context=context,
            reject_non_json=reject_non_json,
        )
        content: str | list[Any] | tuple[Any, ...] | None
        if isinstance(result.content, str):
            content = await _apply_text_result_policy(result.content, policy, context=context)
        elif result.content is None:
            content = None
        else:
            projected_content: list[Any] = []
            for item in result.content:
                if isinstance(item, str):
                    projected_content.append(await _apply_text_result_policy(item, policy, context=context))
                elif isinstance(item, TextContent):
                    projected_content.append(
                        TextContent(
                            content=await _apply_text_result_policy(item.content, policy, context=context),
                            metadata=(
                                None
                                if item.metadata is None
                                else await _apply_optional_json_result_policy(
                                    item.metadata,
                                    policy,
                                    context=context,
                                    reject_non_json=reject_non_json,
                                )
                            ),
                        )
                    )
                else:
                    projected_content.append(item)
            content = tuple(projected_content) if isinstance(result.content, tuple) else projected_content
        projected_metadata = (
            None
            if result.metadata is None
            else await _apply_optional_json_result_policy(
                result.metadata,
                policy,
                context=context,
                reject_non_json=reject_non_json,
            )
        )
        tools = (
            None
            if result.tools is None
            else [await _apply_text_result_policy(item, policy, context=context) for item in result.tools]
        )
        return ToolReturn(
            return_value=return_value,
            content=content,
            metadata=projected_metadata,
            tools=tools,
        )
    except ToolFailed:
        raise
    except (RecursionError, TypeError, ValueError, ValidationError) as exc:
        raise ToolFailed("Tool returned invalid native content.") from exc


def _render_tool_return(result: ToolReturn) -> ToolReturn:
    """Lower supplemental values to native multimodal tool content at settlement.

    The first item remains the structured execution result. Presentation uses
    that explicit boundary, while native adapters render the complete tool part.
    CodeAct's nested calls defer lowering until the outer runner settles.
    """
    if result.content is None or _NESTED_TOOL_EXECUTION.get() or TOOL_CONTENT_METADATA_KEY in (result.metadata or {}):
        return result
    supplement = [result.content] if isinstance(result.content, str) else result.content
    # CachePoint is native request control, not tool data. Keep those markers
    # in the native supplemental field so adapters apply their cache semantics.
    cache_points = [item for item in supplement if isinstance(item, CachePoint)]
    items = [item for item in supplement if not isinstance(item, CachePoint)]
    projected = [item.content if isinstance(item, TextContent) else item for item in items]
    return replace(
        result,
        return_value=[result.return_value, *projected],
        content=cache_points or None,
        metadata={
            **(result.metadata or {}),
            TOOL_CONTENT_METADATA_KEY: {
                "result_index": 0,
                "items": [
                    item.metadata.model_copy(
                        update={"display": False, "source_id": item.metadata.source_id or "a13n.tool"}
                    ).model_dump(mode="json")
                    for item in content_items(items)
                ],
            },
        },
    )


def tool_execution_value(content: Any, metadata: dict[str, Any] | None) -> Any:
    """Project a settled tool result without leaking native media payloads."""
    if metadata is not None and TOOL_CONTENT_METADATA_KEY in metadata:
        return content[metadata[TOOL_CONTENT_METADATA_KEY]["result_index"]]
    return content


async def _apply_optional_json_result_policy(
    value: Any,
    policy: ToolOutputPolicy,
    *,
    context: AgentContext | None,
    reject_non_json: bool,
) -> Any:
    if not reject_non_json and not _is_native_json_result(value):
        return value
    return await _apply_json_result_policy(value, policy, context=context)


async def _apply_text_result_policy(
    value: str,
    policy: ToolOutputPolicy,
    *,
    context: AgentContext | None,
) -> str:
    if not isinstance(value, str):
        raise TypeError("native text fields must be strings")
    safe_value = redact_bearer(value) if policy.redact else value
    encoded = safe_value.encode("utf-8")
    output_chars = len(safe_value)
    output_bytes = len(encoded)
    if output_chars <= FINAL_TOOL_OUTPUT_HARD_CHARS and output_bytes <= policy.max_inline_bytes:
        return safe_value
    if policy.overflow == "fail":
        raise ToolFailed("Tool result exceeded its output limit.")

    output_file_path: str | None = None
    if policy.overflow == "spill" and output_bytes <= policy.max_output_bytes and context is not None:
        output_file_path = await context._spill_tool_result(encoded, suffix=".txt")
    marker = (
        f"\n[tool result truncated; output_chars={output_chars}; output_bytes={output_bytes}; "
        f"output_file_path={output_file_path or 'unavailable'}]\n"
    )
    return _bounded_head_tail_text(
        safe_value,
        marker=marker,
        char_limit=FINAL_TOOL_OUTPUT_HARD_CHARS,
        byte_limit=policy.max_inline_bytes,
    )


def _bounded_json_preview(
    value: JsonValue,
    *,
    output_chars: int,
    output_bytes: int,
    output_file_path: str | None,
    head: bytes,
    tail: bytes,
    char_limit: int,
    byte_limit: int,
) -> dict[str, JsonValue]:
    leaf_limit = max(8, min(char_limit, byte_limit) // 4)
    while leaf_limit >= 8:
        result = _truncate_json_strings(value, leaf_limit)
        envelope: dict[str, JsonValue] = {
            "result": result,
            "truncated": True,
            "output_chars": output_chars,
            "output_bytes": output_bytes,
            "output_file_path": output_file_path,
        }
        if _json_fits(envelope, char_limit=char_limit, byte_limit=byte_limit):
            return envelope
        leaf_limit //= 2

    head_text = head.decode("utf-8", errors="ignore")
    tail_text = tail.decode("utf-8", errors="ignore")
    omitted_chars = max(0, output_chars - len(head_text) - len(tail_text))
    omitted_bytes = max(0, output_bytes - len(head) - len(tail))
    preview = f"{head_text}\n[... {omitted_chars} characters / {omitted_bytes} bytes omitted ...]\n{tail_text}"
    envelope = {
        "result": "",
        "truncated": True,
        "output_chars": output_chars,
        "output_bytes": output_bytes,
        "output_file_path": output_file_path,
    }
    low = 0
    high = len(preview)
    while low <= high:
        retained = (low + high) // 2
        candidate = _head_tail_chars(preview, retained)
        envelope["result"] = candidate
        if _json_fits(envelope, char_limit=char_limit, byte_limit=byte_limit):
            low = retained + 1
        else:
            high = retained - 1
    envelope["result"] = _head_tail_chars(preview, max(0, high))
    return envelope


def _truncate_json_strings(value: JsonValue, limit: int) -> JsonValue:
    if isinstance(value, str):
        if len(value) <= limit and len(value.encode("utf-8")) <= limit:
            return value
        marker = "\n[... truncated ...]\n"
        return _bounded_head_tail_text(
            value,
            marker=marker,
            char_limit=limit,
            byte_limit=limit,
        )
    if isinstance(value, list):
        return [_truncate_json_strings(item, limit) for item in value]
    if isinstance(value, dict):
        return {key: _truncate_json_strings(item, limit) for key, item in value.items()}
    return value


def _bounded_head_tail_text(
    value: str,
    *,
    marker: str,
    char_limit: int,
    byte_limit: int,
) -> str:
    if char_limit <= 0 or byte_limit <= 0:
        return ""
    if len(marker) > char_limit or len(marker.encode("utf-8")) > byte_limit:
        return _bounded_text_prefix(marker, char_limit=char_limit, byte_limit=byte_limit)
    if len(value) <= char_limit and len(value.encode("utf-8")) <= byte_limit:
        return value
    low = 0
    high = len(value)
    while low <= high:
        retained = (low + high) // 2
        head_size = retained // 2
        tail_size = retained - head_size
        tail = value[-tail_size:] if tail_size else ""
        candidate = f"{value[:head_size]}{marker}{tail}"
        if len(candidate) <= char_limit and len(candidate.encode("utf-8")) <= byte_limit:
            low = retained + 1
        else:
            high = retained - 1
    head_size = max(0, high) // 2
    tail_size = max(0, high) - head_size
    tail = value[-tail_size:] if tail_size else ""
    return f"{value[:head_size]}{marker}{tail}"


def _head_tail_chars(value: str, retained: int) -> str:
    head_size = retained // 2
    tail_size = retained - head_size
    tail = value[-tail_size:] if tail_size else ""
    return f"{value[:head_size]}{tail}"


def _bounded_text_prefix(value: str, *, char_limit: int, byte_limit: int) -> str:
    low = 0
    high = min(len(value), char_limit)
    while low <= high:
        selected = (low + high) // 2
        if len(value[:selected].encode("utf-8")) <= byte_limit:
            low = selected + 1
        else:
            high = selected - 1
    return value[: max(0, high)]


def _json_fits(value: JsonValue, *, char_limit: int, byte_limit: int) -> bool:
    text = dump_json_text(value)
    return len(text) <= char_limit and len(text.encode("utf-8")) <= byte_limit


def _scan_json(value: JsonValue, *, keep_bytes: int) -> tuple[int, int, bytes, bytes]:
    total_chars = 0
    total_bytes = 0
    head = bytearray()
    tail = bytearray()
    for chunk in _iter_json(value, redact=False):
        total_chars += len(chunk.decode("utf-8"))
        total_bytes += len(chunk)
        if len(head) < keep_bytes:
            head.extend(chunk[: keep_bytes - len(head)])
        if keep_bytes:
            tail.extend(chunk)
            if len(tail) > keep_bytes:
                del tail[: len(tail) - keep_bytes]
    return total_chars, total_bytes, bytes(head), bytes(tail)


def _project_json_result(result: Any) -> JsonValue:
    if not _is_native_json(result):
        raise TypeError("Tool results must be native JSON values")
    return cast(JsonValue, result)


def _is_native_json_result(value: Any) -> bool:
    try:
        return _is_native_json(value)
    except ValueError:
        return False


def _is_native_json(value: Any, active: set[int] | None = None) -> bool:
    if value is None or isinstance(value, str | bool | int):
        return True
    if isinstance(value, float):
        require_finite_json(value)
        return True
    if not isinstance(value, list | dict):
        return False

    active = active if active is not None else set()
    value_id = id(value)
    if value_id in active:
        raise ValueError("JSON values cannot contain cycles")
    active.add(value_id)
    try:
        if isinstance(value, list):
            return all(_is_native_json(item, active) for item in value)
        return all(isinstance(key, str) and _is_native_json(item, active) for key, item in value.items())
    finally:
        active.remove(value_id)


def _iter_json(value: JsonValue, *, redact: bool) -> Iterator[bytes]:
    if value is None:
        yield b"null"
    elif value is True:
        yield b"true"
    elif value is False:
        yield b"false"
    elif isinstance(value, int | float):
        yield json.dumps(value, allow_nan=False).encode("ascii")
    elif isinstance(value, str):
        yield from _iter_json_string(redact_bearer(value) if redact else value)
    elif isinstance(value, list):
        yield b"["
        for index, item in enumerate(value):
            if index:
                yield b","
            yield from _iter_json(item, redact=redact)
        yield b"]"
    else:
        yield b"{"
        for index, (key, item) in enumerate(value.items()):
            if index:
                yield b","
            yield from _iter_json_string(key)
            yield b":"
            if redact and is_sensitive_key(key):
                yield b'"[REDACTED]"'
            else:
                yield from _iter_json(item, redact=redact)
        yield b"}"


def _iter_json_string(value: str, *, chunk_size: int = 4096) -> Iterator[bytes]:
    yield b'"'
    for offset in range(0, len(value), chunk_size):
        encoded = json.dumps(value[offset : offset + chunk_size], ensure_ascii=False)[1:-1]
        yield encoded.encode("utf-8")
    yield b'"'


class _ToolResultSpillStore:
    """Best-effort run-private spill directories pinned to their original selections."""

    def __init__(self, context: AgentContext) -> None:
        run_digest = hashlib.sha256(context.run_id.encode("utf-8")).hexdigest()[:12]
        self._environment = context.environment
        self._directory = context.tool_result_directory
        self._run_directory_prefix = f"run-{run_digest}"
        self._directories: dict[tuple[str, str], tuple[FileScopeSelection, str]] = {}
        self._next_sequence = 1
        self._closed = False
        self._lock = asyncio.Lock()

    async def write(self, data: bytes, *, suffix: str) -> str | None:
        if suffix not in {".json", ".txt"}:
            raise ValueError("tool result spill suffix is invalid")
        async with self._lock:
            if self._closed:
                return None
            try:
                directory = self._new_directory()
                if directory is None:
                    return None
                selection = await self._environment.resolve_files(directory)
                key = (selection.resolved_path.mount_id, selection.observed_generation)
                record = self._directories.get(key)
                if record is not None:
                    selection, directory = record
                async with self._environment.open_files(selection) as files:
                    if record is None:
                        parent = directory.rsplit("/", 1)[0]
                        await files.mkdir(parent, parents=True, exist_ok=True)
                        await files.mkdir(directory, exist_ok=False)
                        # Own the leaf as soon as it exists, including failed writes.
                        self._directories[key] = (selection, directory)
                    path = f"{directory}/tool-result-{self._next_sequence}{suffix}"
                    self._next_sequence += 1
                    await files.write_bytes_stream(path, _byte_chunks(data), mode="create")
                current = self._environment.select_files(directory)
                if (
                    current.resolved_path != selection.resolved_path
                    or current.observed_generation != selection.observed_generation
                ):
                    return None
            except Exception:
                return None
            return path

    def _new_directory(self) -> str | None:
        parent = self._directory
        if parent is None:
            snapshot = self._environment.snapshot
            mount = next((item for item in snapshot.mounts if item.name == snapshot.default_mount), None)
            if mount is None:
                return None
            root = mount.mount_path or f"/environment/{mount.name}"
            parent = f"{root.rstrip('/')}/.a13n/tmp/tool-results"
        return f"{parent.rstrip('/')}/{self._run_directory_prefix}-{uuid4().hex[:12]}"

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            for selection, directory in self._directories.values():
                try:
                    async with self._environment.open_files(selection) as files:
                        await files.remove(directory, recursive=True)
                except Exception:
                    # Retired or unavailable selections may leave files behind. Never
                    # reselect a replacement or turn completed effects into retries.
                    continue
            self._directories.clear()


async def _byte_chunks(data: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(data), 64 * 1024):
        yield data[offset : offset + 64 * 1024]
