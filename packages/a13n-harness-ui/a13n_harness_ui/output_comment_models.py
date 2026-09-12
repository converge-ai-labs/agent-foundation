"""Detached saved-output locators and immutable human comment publications."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CommentModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class RootOutputLocation(CommentModel):
    kind: Literal["root_text"] = "root_text"
    message: int = Field(ge=0)
    part: int = Field(ge=0)


class ChildOutputLocation(CommentModel):
    kind: Literal["child_text"] = "child_text"
    execution_id: str = Field(min_length=1, max_length=80)
    # None selects the checkpoint's final answer, not an activity position.
    activity: int | None = Field(default=None, ge=0)


class SavedOutputTarget(CommentModel):
    producing_thread_id: str = Field(min_length=1, max_length=80)
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    location: Annotated[RootOutputLocation | ChildOutputLocation, Field(discriminator="kind")]


class CommentSelection(CommentModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=16 * 1024)

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.end <= self.start or self.end - self.start != len(self.quote):
            raise ValueError("selection must match a nonempty Unicode code-point range")
        return self


class CommentAuthor(CommentModel):
    display_name: str = Field(min_length=1, max_length=80)
    participant_id: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("display_name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("display name must be nonempty and NUL-free")
        return value


class CommentPublication(CommentModel):
    comment_id: str = Field(pattern=r"^comment-[A-Za-z0-9_-]{16,64}$")
    target: SavedOutputTarget
    selection: CommentSelection | None = None
    author: CommentAuthor
    body: str = Field(min_length=1, max_length=16 * 1024)

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("comment body must be nonempty and NUL-free")
        return value


class OutputComment(CommentPublication):
    root_thread_id: str = Field(min_length=1, max_length=80)
    created_at: datetime


class CommentPage(CommentModel):
    comments: tuple[OutputComment, ...]
    next_cursor: str | None = None


class SavedOutputView(CommentModel):
    target: SavedOutputTarget
    text: str = Field(max_length=64 * 1024)
    offset: int = Field(ge=0)
    total_characters: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class SavedChildOutputPage(CommentModel):
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    outputs: tuple[SavedOutputView, ...]
    total: int = Field(ge=0)
    next_cursor: str | None = None
