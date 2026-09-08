"""Validated public configuration for restricted CodeAct execution."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class CodeActConfig:
    """Configure the optional restricted Python orchestration capability."""

    inline: bool = True
    programs: bool = True
    max_source_bytes: int = 256 * 1024
    max_output_bytes: int = 10 * 1024 * 1024
    max_state_bytes: int = 10 * 1024 * 1024
    max_state_entries: int = 256
    max_tool_calls: int = 128
    max_concurrency: int = 16
    timeout_seconds: float = 300.0
    max_memory_bytes: int = 100 * 1024 * 1024
    max_recursion_depth: int = 1000

    def __post_init__(self) -> None:
        if not isinstance(self.inline, bool) or not isinstance(self.programs, bool):
            raise TypeError("CodeAct runner switches must be booleans")
        if not self.inline and not self.programs:
            raise ValueError("CodeActConfig must enable inline execution, programs, or both")
        for field_name in (
            "max_source_bytes",
            "max_output_bytes",
            "max_state_bytes",
            "max_state_entries",
            "max_tool_calls",
            "max_concurrency",
            "max_memory_bytes",
            "max_recursion_depth",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if (
            not isinstance(self.timeout_seconds, int | float)
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive and finite")
