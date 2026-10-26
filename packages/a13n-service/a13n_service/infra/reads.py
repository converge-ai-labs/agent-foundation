"""Bounded query representations, resumable text and snapshot-bound collection positions."""

import hashlib
import json
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from typing import Annotated, Literal

from fastapi import Query
from pydantic import BaseModel, Field, JsonValue
from pydantic_core import to_jsonable_python

from a13n_service.infra import cursors
from a13n_service.infra.errors import ServiceError, invalid

# Set only by the in-process MCP adapter, never from caller HTTP headers.
# Response projections consult this flag; durable writes retain their complete values.
tool_response: ContextVar[bool] = ContextVar("tool_response", default=False)

type ReadView = Literal["detailed", "compact"]
View = Annotated[ReadView, Query(description="Use compact for discovery; detailed preserves the original response.")]
Offset = Annotated[int, Query(ge=0, description="Unicode code-point offset; continue with next_offset.")]
TextLimit = Annotated[int, Query(ge=1, le=4000, description="Maximum Unicode code points in this fragment.")]
Version = Annotated[
    str | None, Query(max_length=64, description="Content version from the first read; required after offset 0.")
]
IndexQuery = Annotated[
    str | None,
    Query(min_length=1, max_length=256, description="Case-insensitive substring of full names and descriptions."),
]
Search = Annotated[str | None, Query(min_length=1, max_length=256, description="Case-sensitive literal text search.")]
Names = Annotated[
    list[str],
    Query(min_length=1, max_length=5, description="Exact names; repeat name to select up to five definitions."),
]

# Leave space for HTTP/MCP metadata and serialized text escaping below the Harness hard ceiling.
BODY_CHARS = 10_000


def _canonical(value: object) -> object:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        return sorted(
            (_canonical(item) for item in value), key=lambda item: json.dumps(to_jsonable_python(item), sort_keys=True)
        )
    if isinstance(value, list | tuple):
        return [_canonical(item) for item in value]
    return value


def json_text(value: object) -> str:
    return json.dumps(
        to_jsonable_python(_canonical(value)),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def size(value: object) -> int:
    return len(json_text(value))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Preview(BaseModel):
    text: str
    truncated: bool


def preview(value: object, limit: int = 300) -> Preview:
    text = value if isinstance(value, str) else "" if value is None else json_text(value)
    return Preview(text=text[:limit], truncated=len(text) > limit)


def bounded[T: BaseModel](value: T, *, budget: int = 4000) -> T:
    """Shorten only declared Preview text, preserving exact identity and state even for heavily escaped input."""

    def previews(model: BaseModel) -> list[Preview]:
        found = []
        for name in type(model).model_fields:
            field = getattr(model, name)
            if isinstance(field, Preview):
                found.append(field)
            elif isinstance(field, BaseModel):
                found.extend(previews(field))
        return found

    fields = previews(value)
    while size(value) > budget:
        longest = max(fields, key=lambda field: len(field.text), default=None)
        if longest is None or not longest.text:
            raise ServiceError("payload_too_large", "Resource identifiers exceed the compact response budget")
        longest.text = longest.text[: len(longest.text) // 2]
        longest.truncated = True
    return value


class TextRead(BaseModel):
    """A fragment of a versioned value, not a complete JSON object or writable configuration."""

    representation: Literal["fragment"] = "fragment"
    source: str
    version: str
    media_type: Literal["text/plain", "application/json"]
    offset: int
    text: str
    total_chars: int
    next_offset: int | None
    has_more: bool
    query: str | None = None
    match_offset: int | None = None
    source_truncated: bool = False


def read_value(
    value: object,
    *,
    source: str,
    offset: int = 0,
    limit: int = 4000,
    version: str | None = None,
    query: str | None = None,
    source_truncated: bool = False,
) -> TextRead:
    """Slice literal text or canonical JSON; changed content rejects continuation instead of mixing versions."""
    text = value if isinstance(value, str) else json_text(value)
    current = digest(text)
    if offset < 0 or not 1 <= limit <= 4000:
        raise invalid("offset", "offset must be non-negative and limit must be between 1 and 4000")
    if offset and version is None:
        raise invalid("version", "Continuation requires the version returned by the first read")
    if version is not None and version != current:
        raise ServiceError("conflict", "Content changed; restart the read", {"reason": "content_changed"})
    if offset > len(text):
        raise invalid("offset", "Offset exceeds the content length")
    match = None
    start = offset
    if query is not None:
        if not query or len(query) > 256:
            raise invalid("query", "Use 1 to 256 characters")
        match = text.find(query, offset)
        start = len(text) if match < 0 else max(offset, match - min(120, limit // 4))
    end = min(len(text), start + limit)
    result = TextRead(
        source=source,
        version=current,
        media_type="text/plain" if isinstance(value, str) else "application/json",
        offset=start,
        text=text[start:end],
        total_chars=len(text),
        next_offset=None,
        has_more=False,
        query=query,
        match_offset=match if match is not None and match >= 0 else None,
        source_truncated=source_truncated,
    )
    # Escaped control characters can cost six serialized characters each.
    while size(result) > BODY_CHARS - 100 and len(result.text) > 1:
        result.text = result.text[: max(1, len(result.text) // 2)]
        end = start + len(result.text)
    following = end
    if query is not None:
        following = len(text) if match is None or match < 0 else match + 1
        result.has_more = text.find(query, following) >= 0
    else:
        result.has_more = following < len(text)
    result.next_offset = following if result.has_more else None
    return result


def page_limit(view: ReadView, limit: int | None, detailed: int = 50) -> int:
    return limit if limit is not None else 10 if view == "compact" else detailed


def fit_count(items: Sequence[BaseModel], *, overhead: int = 1200) -> int:
    """Keep whole records. Callers issue their own continuation at the last returned position."""
    used = overhead
    count = 0
    for item in items:
        cost = size(item) + 1
        if used + cost > BODY_CHARS:
            break
        used += cost
        count += 1
    if items and count == 0:
        raise ServiceError("payload_too_large", "A summary exceeds the response budget; read the resource directly")
    return count


def snapshot_page[T: BaseModel](
    items: Sequence[T],
    *,
    key: Callable[[T], str],
    scope: str,
    filters: object,
    cursor: str | None,
    limit: int,
    revision: str,
) -> tuple[list[T], str | None]:
    """Paginate a sorted snapshot. A provider/catalog refresh invalidates its old cursors explicitly."""
    owner = cursors.query_owner(scope, filters, revision)
    ordered = sorted(items, key=key)
    after = cursors.id_position(cursor, "read_index", owner, max_length=1024)
    candidates = [item for item in ordered if key(item) > after][: limit + 1]
    count = fit_count(candidates[:limit])
    selected = candidates[:count]
    following = cursors.encode("read_index", owner, key(selected[-1])) if len(candidates) > count else None
    return selected, following


class DefinitionReference(BaseModel):
    name: str
    version: str
    size_chars: int
    instruction: str = (
        "Read this endpoint with one name, offset=0 and limit=4000, then continue with version and next_offset."
    )


class Definition(BaseModel):
    name: str
    definition: dict[str, JsonValue]


class Definitions(BaseModel):
    items: list[Definition] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    oversized: list[DefinitionReference] = Field(default_factory=list)


def definitions(
    values: dict[str, BaseModel],
    names: list[str],
    *,
    source: str,
    offset: int | None = None,
    limit: int = 4000,
    version: str | None = None,
    query: str | None = None,
) -> Definitions | TextRead:
    if not 1 <= len(names) <= 5 or len(set(names)) != len(names) or any(not name or len(name) > 1024 for name in names):
        raise invalid("name", "Select one to five unique exact names")
    if offset is not None:
        if len(names) != 1:
            raise invalid("name", "Fragment reads require exactly one name")
        if names[0] not in values:
            raise ServiceError("not_found", "Definition not found", {"name": names[0]})
        return read_value(values[names[0]], source=source, offset=offset, limit=limit, version=version, query=query)
    if version is not None or query is not None:
        raise invalid("offset", "Provide offset=0 for a fragment or search")
    result = Definitions(missing=[name for name in names if name not in values])
    encoded = {name: json_text(values[name]) for name in names if name in values}
    result.oversized = [
        DefinitionReference(name=name, version=digest(text), size_chars=len(text)) for name, text in encoded.items()
    ]
    if size(result) > BODY_CHARS:
        raise invalid("name", "Selected names exceed the response budget; select fewer names")
    for name in encoded:
        item = Definition(name=name, definition=values[name].model_dump(mode="json"))
        reference = next(ref for ref in result.oversized if ref.name == name)
        if size(result) + size(item) - size(reference) <= BODY_CHARS - 200:
            result.items.append(item)
            result.oversized.remove(reference)
    return result
