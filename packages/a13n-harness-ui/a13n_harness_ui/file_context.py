"""Detached provenance for human-selected native file input."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_INLINE_CONTEXT_BYTES = 64 * 1024


class FileContextSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    location: Literal["host"] = "host"
    path: str = Field(min_length=1, max_length=4096)
    resolved_path: str = Field(min_length=1, max_length=4096)
    revision: str = Field(min_length=1, max_length=128)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


def context_text(source: FileContextSource, data: bytes) -> str | None:
    """Return the same attributed text for submission and text-only steering."""
    if len(data) > MAX_INLINE_CONTEXT_BYTES or b"\x00" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return f"Selected Host file context (source: {source.model_dump_json()}):\n{text}"
