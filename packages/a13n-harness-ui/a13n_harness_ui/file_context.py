"""Detached provenance for human-selected, immutable input context."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from a13n_harness_ui.output_comment_models import SavedOutputTarget

MAX_INLINE_CONTEXT_BYTES = 64 * 1024


class FileContextSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    location: Literal["host"] = "host"
    path: str = Field(min_length=1, max_length=4096)
    resolved_path: str = Field(min_length=1, max_length=4096)
    revision: str = Field(min_length=1, max_length=128)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class GitContextSource(BaseModel):
    """A reviewed Git comparison, not a revision of one mutable file."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kind: Literal["git_diff"] = "git_diff"
    location: Literal["host"] = "host"
    repository_path: str = Field(min_length=1, max_length=4096)
    git_dir: str = Field(min_length=1, max_length=4096)
    path: str = Field(min_length=1, max_length=4096)
    original_path: str | None = None
    comparison: Literal["staged", "unstaged", "untracked"]
    head_oid: str | None
    index_revision: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=1, max_length=128)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class CommentContextSource(BaseModel):
    """A complete published comment and its exact saved assistant output."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kind: Literal["comment_reference"] = "comment_reference"
    root_thread_id: str = Field(min_length=1, max_length=80)
    comment_id: str = Field(pattern=r"^comment-[A-Za-z0-9_-]{16,64}$")
    target: SavedOutputTarget


CapturedSource = FileContextSource | GitContextSource | CommentContextSource


def context_text(source: CapturedSource, data: bytes) -> str | None:
    """Return the same attributed text for submission and steering."""
    if len(data) > MAX_INLINE_CONTEXT_BYTES or b"\x00" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if isinstance(source, CommentContextSource):
        return text
    kind = "Git diff" if isinstance(source, GitContextSource) else "file"
    return f"Selected Host {kind} context (source: {source.model_dump_json()}):\n{text}"
