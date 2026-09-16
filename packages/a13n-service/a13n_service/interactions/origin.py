"""Trusted submission metadata supplied by internal entry points."""

from dataclasses import dataclass

from .domain import JsonObject


@dataclass(frozen=True, slots=True)
class SubmissionOrigin:
    trigger_type: str = "user_input"
    native_tool_contexts: tuple[JsonObject, ...] = ()
