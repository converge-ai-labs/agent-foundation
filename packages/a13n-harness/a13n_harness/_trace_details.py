"""Small, bounded projections of execution facts; never a replay or storage format."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from itertools import islice
from math import isfinite
from typing import Literal

from opentelemetry.trace import Span
from pydantic import BaseModel, JsonValue
from pydantic_ai.messages import AudioUrl, BinaryContent, CachePoint, DocumentUrl, ImageUrl, TextContent, VideoUrl

_MAX_CONTENT_BYTES = 8192


def _clip(value: str, limit: int) -> str:
    return value[:limit].encode("utf-8", errors="replace")[:limit].decode("utf-8", errors="ignore")


def _project(value: object, budget: list[int], depth: int = 0) -> JsonValue:
    if budget[0] <= 0 or depth > 6:
        budget[1] = 1
        return "[omitted]"
    budget[0] -= 1
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        clipped = _clip(value, _MAX_CONTENT_BYTES)
        if clipped != value:
            budget[1] = 1
        return clipped
    if isinstance(value, int):
        if -(2**63) <= value < 2**63:
            return value
        budget[1] = 1
        return "[integer omitted]"
    if isinstance(value, float):
        if isfinite(value):
            return value
        budget[1] = 1
        return "[non-finite number omitted]"
    if isinstance(value, TextContent):
        return _project(value.content, budget, depth)
    if isinstance(value, CachePoint):
        return {"type": "cache_point"}
    # Media is descriptive only, even in full-content mode. Never fetch URLs or encode bytes.
    if isinstance(value, BinaryContent):
        return {"type": "attachment", "media_type": _clip(value.media_type, 128)}
    if isinstance(value, (DocumentUrl, ImageUrl, AudioUrl, VideoUrl)):
        return {"type": "attachment", "kind": type(value).__name__}
    if isinstance(value, BaseModel):
        value = {key: item for key, item in value.__dict__.items() if key in type(value).model_fields}
    if isinstance(value, Mapping):
        if len(value) > 64:
            budget[1] = 1
        result: dict[str, JsonValue] = {}
        for key, item in islice(value.items(), 64):
            if budget[0] <= 0:
                budget[1] = 1
                break
            if isinstance(key, str):
                result[_clip(key, 128)] = _project(item, budget, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > 64:
            budget[1] = 1
        result_list: list[JsonValue] = []
        for item in value[:64]:
            if budget[0] <= 0:
                budget[1] = 1
                break
            result_list.append(_project(item, budget, depth + 1))
        return result_list
    budget[1] = 1
    return {"type": _clip(type(value).__name__, 128), "omitted": True}


def record_content(
    span: Span,
    direction: Literal["input", "output"],
    value: object,
    *,
    include_content: bool,
    kind: str,
) -> None:
    """Record bounded content on its owner, without repr, binary, or arbitrary serialization."""
    if not span.is_recording():
        return
    try:
        span.set_attribute(f"a13n.{direction}.kind", kind)
        span.set_attribute(f"a13n.{direction}.type", type(value).__name__)
        if direction == "input" and isinstance(value, (list, tuple)):
            span.set_attribute("a13n.input.part_count", len(value))
            span.set_attribute(
                "a13n.input.attachment_count",
                sum(isinstance(item, (BinaryContent, DocumentUrl, ImageUrl, AudioUrl, VideoUrl)) for item in value),
            )
        if not include_content or value is None:
            span.set_attribute(f"a13n.{direction}.capture", "content_disabled" if not include_content else "no_value")
            return
        budget = [128, 0]
        # Hosts may normalize a text prompt into one native UserContent part.
        content = (
            value[0]
            if direction == "input"
            and isinstance(value, (list, tuple))
            and len(value) == 1
            and isinstance(value[0], str)
            else value
        )
        projected = _project(content, budget)
        encoded = json.dumps(projected, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        truncated = bool(budget[1])
        if len(encoded.encode("utf-8")) > _MAX_CONTENT_BYTES:
            # A valid JSON preview, rather than a cut-off structured object.
            preview = _clip(encoded, _MAX_CONTENT_BYTES // 2)
            encoded = json.dumps({"preview": preview}, ensure_ascii=False, separators=(",", ":"))
            while len(encoded.encode("utf-8")) > _MAX_CONTENT_BYTES:
                preview = preview[: len(preview) // 2]
                encoded = json.dumps({"preview": preview}, ensure_ascii=False, separators=(",", ":"))
            truncated = True
        span.set_attribute(f"a13n.{direction}.capture", "truncated" if truncated else "captured")
        span.set_attribute(f"a13n.{direction}.truncated", truncated)
        span.set_attribute(f"a13n.{direction}", encoded)
        span.set_attribute(f"langfuse.observation.{direction}", encoded)
    except Exception:
        pass


class SkillObservation:
    """One owning span's bounded catalog and observed reads, not proof of skill use."""

    def __init__(self, span: Span) -> None:
        self._span = span
        self._accessed: list[str] = []
        self._access_count = 0
        self._omitted = False

    def catalog(self, names: Sequence[str], *, count: int | None = None) -> None:
        if not self._span.is_recording():
            return
        try:
            values = [_clip(name, 256) for name in names[:16]]
            total = len(names) if count is None else count
            self._span.set_attribute("a13n.skills.available", values)
            self._span.set_attribute("a13n.skills.available_count", total)
            self._span.set_attribute("a13n.skills.available_omitted", max(0, total - len(values)))
            self._span.set_attribute("langfuse.observation.metadata.skills_available", json.dumps(values))
        except Exception:
            pass

    def access(self, name: str) -> None:
        if not self._span.is_recording():
            return
        try:
            self._access_count += 1
            name = _clip(name, 256)
            if name not in self._accessed:
                if len(self._accessed) < 16:
                    self._accessed.append(name)
                else:
                    self._omitted = True
            self._span.set_attribute("a13n.skills.accessed", tuple(self._accessed))
            self._span.set_attribute("a13n.skills.access_count", self._access_count)
            self._span.set_attribute("a13n.skills.accessed_truncated", self._omitted)
            self._span.set_attribute("langfuse.observation.metadata.skills_accessed", json.dumps(self._accessed))
        except Exception:
            pass
