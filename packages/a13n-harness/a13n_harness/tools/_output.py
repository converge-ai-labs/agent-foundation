"""Internal process-local acknowledgement for proactively bounded Toolset output."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

DEFAULT_TOOL_OUTPUT_CHARS = 12_000
FINAL_TOOL_OUTPUT_HARD_CHARS = 20_000


class AcknowledgedToolOutput(dict[str, JsonValue]):
    """Ordinary JSON mapping marked as proactively bounded by its Toolset."""


def acknowledge_tool_output(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Attach the process-local acknowledgement without changing model-visible shape."""
    return cast(dict[str, JsonValue], AcknowledgedToolOutput(dict(value)))


def is_acknowledged_tool_output(value: object) -> bool:
    """Return whether a result carries the Harness-owned acknowledgement type."""
    return isinstance(value, AcknowledgedToolOutput)


__all__ = [
    "DEFAULT_TOOL_OUTPUT_CHARS",
    "FINAL_TOOL_OUTPUT_HARD_CHARS",
    "acknowledge_tool_output",
    "is_acknowledged_tool_output",
]
