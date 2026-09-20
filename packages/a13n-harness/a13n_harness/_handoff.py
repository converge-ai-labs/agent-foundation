"""Shared handoff state and value helpers, independent of Capability and Toolset hooks."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from a13n_harness.errors import HarnessError

HANDOFF_CAPABILITY_ID = "a13n.handoff"
_HANDOFF_STATE_VERSION = "1"


class _HandoffState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: str | None = Field(default=None, min_length=1, max_length=128)
    summary: str | None = None
    files: tuple[str, ...] = Field(default=(), max_length=64)
    kind: Literal["handoff", "compaction"] = Field(default="handoff", exclude=True)
    preserve_recent_user_turns: int = Field(default=0, ge=0, le=32, exclude=True)
    target_tokens: int | None = Field(default=None, gt=0, exclude=True)

    @field_validator("files")
    @classmethod
    def _validate_files(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("file references must be unique")
        if any(not path.strip() or "\x00" in path for path in value):
            raise ValueError("file reference is invalid")
        return tuple(value)

    @model_validator(mode="after")
    def _validate_operation_identity(self) -> _HandoffState:
        if self.operation_id is None:
            return self
        expected_prefix = "compaction-" if self.kind == "compaction" else "handoff-"
        if not self.operation_id.startswith(expected_prefix):
            raise ValueError("handoff operation identity is invalid")
        return self


def _safe_context_error_code(exc: BaseException) -> str:
    if isinstance(exc, HarnessError) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", exc.code):
        return exc.code
    return "context_operation_failed"


def _render_summary(content: str) -> str:
    stripped = content.strip()
    return stripped if stripped.startswith("# Context Summary") else f"# Context Summary\n\n{stripped}"
