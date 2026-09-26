"""Shared handoff state and value helpers, independent of Capability and Toolset hooks."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from a13n_harness.errors import HarnessError

HANDOFF_CAPABILITY_ID = "a13n.handoff"
_HANDOFF_STATE_VERSION = "1"


class _HandoffState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: str | None = Field(default=None, min_length=1, max_length=128)
    summary: str | None = None
    files: tuple[str, ...] = Field(default=(), max_length=64)

    @model_validator(mode="before")
    @classmethod
    def _decode_legacy_state(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if value.get("kind", "handoff") not in {"handoff", "compaction"}:
            raise ValueError("handoff kind is invalid")
        if value.get("kind") == "compaction":
            # Old compaction state is not a pending semantic handoff.
            return {}
        return {
            key: item
            for key, item in value.items()
            if key not in {"kind", "preserve_recent_user_turns", "target_tokens"}
        }

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
        if not self.operation_id.startswith("handoff-"):
            raise ValueError("handoff operation identity is invalid")
        return self


def _safe_context_error_code(exc: BaseException) -> str:
    if isinstance(exc, HarnessError) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", exc.code):
        return exc.code
    return "context_operation_failed"


def _render_summary(content: str) -> str:
    stripped = content.strip()
    return stripped if stripped.startswith("# Context Summary") else f"# Context Summary\n\n{stripped}"
