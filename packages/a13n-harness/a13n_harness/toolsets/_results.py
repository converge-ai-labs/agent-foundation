"""Shared JSON result primitives for model-facing Toolsets."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from pydantic import JsonValue


class ToolError(TypedDict):
    code: str
    retry_hint: NotRequired[str]
    details: NotRequired[dict[str, JsonValue]]
    max_bytes: NotRequired[int]
    key: NotRequired[str]
    outcome_known: NotRequired[bool]
    status_code: NotRequired[int]
    reason: NotRequired[str | None]


class ToolFailure(TypedDict):
    ok: Literal[False]
    error: ToolError


__all__ = ["ToolError", "ToolFailure"]
