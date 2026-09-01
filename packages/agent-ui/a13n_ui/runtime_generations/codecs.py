"""Lossless JSON codecs for private Runner execution values."""

from __future__ import annotations

import json
from typing import Any

from pydantic import JsonValue, TypeAdapter
from pydantic_ai import ModelRetry, ToolApproved, ToolDenied, ToolFailed, ToolReturn
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.tools import DeferredToolResults

_CALL_RESULT_ADAPTER = TypeAdapter(ToolReturn | ToolFailed | ModelRetry | RetryPromptPart)
_APPROVAL_ADAPTER = TypeAdapter(ToolApproved | ToolDenied)


def dump_deferred_results(results: DeferredToolResults) -> JsonValue:
    """Serialize native deferred values while preserving their tagged variants."""

    return TypeAdapter(DeferredToolResults).dump_python(results, mode="json", by_alias=True)


def load_deferred_results(value: JsonValue) -> DeferredToolResults:
    """Restore native tagged variants without coercing ordinary JSON call results."""

    if not isinstance(value, dict):
        raise ValueError("deferred results must be an object")
    raw_calls = value.get("calls", {})
    raw_approvals = value.get("approvals", {})
    raw_metadata = value.get("metadata", {})
    if not isinstance(raw_calls, dict) or not isinstance(raw_approvals, dict) or not isinstance(raw_metadata, dict):
        raise ValueError("deferred result collections must be objects")

    calls: dict[str, Any] = {}
    for call_id, result in raw_calls.items():
        if not isinstance(call_id, str):
            raise ValueError("deferred call IDs must be strings")
        kind = result.get("kind") if isinstance(result, dict) else None
        calls[call_id] = (
            _CALL_RESULT_ADAPTER.validate_json(json.dumps(result), strict=True)
            if kind in {"tool-return", "tool-failed", "model-retry", "retry-prompt"}
            else result
        )

    approvals: dict[str, bool | ToolApproved | ToolDenied] = {}
    for call_id, result in raw_approvals.items():
        if not isinstance(call_id, str):
            raise ValueError("deferred approval IDs must be strings")
        if isinstance(result, bool):
            approvals[call_id] = result
        elif isinstance(result, dict) and result.get("kind") in {"tool-approved", "tool-denied"}:
            approvals[call_id] = _APPROVAL_ADAPTER.validate_json(json.dumps(result), strict=True)
        else:
            raise ValueError("deferred approval result is invalid")

    metadata: dict[str, dict[str, Any]] = {}
    for call_id, item in raw_metadata.items():
        if not isinstance(call_id, str) or not isinstance(item, dict):
            raise ValueError("deferred metadata is invalid")
        metadata[call_id] = item
    return DeferredToolResults(calls=calls, approvals=approvals, metadata=metadata)


__all__ = ["dump_deferred_results", "load_deferred_results"]
