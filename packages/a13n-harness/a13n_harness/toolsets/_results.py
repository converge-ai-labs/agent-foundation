"""Shared JSON failure construction for first-party model-facing Toolsets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, NotRequired, TypedDict, cast

from a13n_environment.models import EnvironmentError
from pydantic import JsonValue, ValidationError

from a13n_harness._tool_observation import record_tool_operation_failure


class ToolError(TypedDict):
    code: str
    message: str
    details: dict[str, JsonValue]
    retry_hint: NotRequired[str]
    max_bytes: NotRequired[int]
    key: NotRequired[str]
    outcome_known: NotRequired[bool]
    status_code: NotRequired[int]
    reason: NotRequired[str | None]


class ToolFailure(TypedDict):
    ok: Literal[False]
    error: ToolError


def tool_failure(
    code: str,
    message: str,
    *,
    details: Mapping[str, JsonValue] | None = None,
    retry_hint: str | None = None,
) -> ToolFailure:
    """Construct a failure from code-owned safe text, not exception descriptions.

    The semantic owner supplies public details. The final invocation boundary still
    owns JSON redaction and output limits. Native framework ToolFailed/ModelRetry
    results and third-party tool payloads are not rewritten into this envelope.
    """
    error = ToolError(code=code, message=message[:512], details=dict(details or {}))
    if retry_hint is not None:
        error["retry_hint"] = retry_hint
    record_tool_operation_failure(code, reason=error["details"].get("reason"))
    return {"ok": False, "error": error}


def environment_failure(exc: EnvironmentError) -> ToolFailure:
    error = cast(ToolError, exc.safe_projection())
    if exc.code == "environment_not_found" and not exc.details.get("hint"):
        error["details"]["hint"] = (
            "Verify the file path or resource reference in the selected mount. For files, use ls or glob on an "
            "existing parent. This is not an outside-mount routing error."
        )
    record_tool_operation_failure(exc.code, reason=error["details"].get("reason"))
    return {"ok": False, "error": error}


def validation_failure(code: str, exc: ValidationError) -> ToolFailure:
    """Expose a bounded field/type pair, never validation input or custom messages."""
    first = exc.errors(include_input=False, include_context=False, include_url=False)[0]
    field = ".".join(str(part) for part in first["loc"])[:128]
    reason = first["type"][:128]
    hint = {
        "missing": "Supply the required field.",
        "string_too_short": "Supply a non-blank string meeting the minimum length.",
        "string_too_long": "Shorten the string to the documented maximum length.",
        "extra_forbidden": "Remove the unrecognized field.",
        "literal_error": "Use one of the values listed in the tool schema.",
    }.get(reason, "Use a value of the documented type and within the field's limits.")
    details: dict[str, JsonValue] = {"reason": reason, "hint": hint}
    if field:
        details["field"] = field
    return tool_failure(code, "Tool input is invalid.", details=details)


__all__ = ["ToolError", "ToolFailure", "environment_failure", "tool_failure", "validation_failure"]
