"""Public diagnostics at tool invocation boundaries, independent of the carrier."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal, NotRequired, TypedDict, cast

from pydantic import JsonValue
from pydantic_ai.exceptions import ToolFailed

from a13n_harness._tool_observation import record_tool_operation_failure, set_tool_span_attributes


class ToolError(TypedDict):
    code: str
    message: str
    details: dict[str, JsonValue]
    source: NotRequired[Literal["system"]]
    retry_hint: NotRequired[str]
    max_bytes: NotRequired[int]
    key: NotRequired[str]
    outcome_known: NotRequired[bool]
    status_code: NotRequired[int]
    reason: NotRequired[str | None]


class ToolFailure(TypedDict):
    ok: Literal[False]
    error: ToolError


INVALID_ARGUMENT = "invalid_argument"
SYSTEM_FAILURE_METADATA_KEY = "a13n.system-failure"


class ReportedToolFailure(dict[str, object]):
    """A local owner explicitly reported failure; business JSON has no such marker."""

    def __init__(self, error: ToolError) -> None:
        super().__init__(ok=False, error=error)
        self.system = error.get("source") == "system"


def reported_failure(error: ToolError, *, stage: Literal["preparation", "execution"] = "execution") -> ToolFailure:
    record_tool_operation_failure(
        error["code"], reason=error["details"].get("reason"), stage=stage, source=error.get("source")
    )
    return cast(ToolFailure, ReportedToolFailure(error))


def system_error(code: str, message: str, *, field: str | None = None, hint: str | None = None) -> ToolError:
    """Only the owner supplies public text. A missing hint implies no recovery instruction."""
    details: dict[str, JsonValue] = {}
    if field:
        details["field"] = field[:128]
    if hint:
        details["hint"] = hint[:1024]
    return ToolError(source="system", code=code, message=message[:512], details=details)


def tool_error_text(error: Mapping[str, object]) -> str:
    """Encode an already public error for native ToolFailed, ModelRetry or MCP text."""
    return json.dumps({"ok": False, "error": error}, ensure_ascii=False, separators=(",", ":"))


def system_failure(code: str, message: str, *, hint: str | None = None) -> ToolFailed:
    """Preserve terminal native failure semantics, including across retained execution."""
    set_tool_span_attributes({"a13n.tool.failure.code": code, "a13n.tool.failure.source": "system"})
    return ToolFailed(tool_error_text(system_error(code, message, hint=hint)))
