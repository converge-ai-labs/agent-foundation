"""Detached locations and paginated views of saved output."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class SavedOutputModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class RootOutputLocation(SavedOutputModel):
    kind: Literal["root_text"] = "root_text"
    message: int = Field(ge=0)
    part: int = Field(ge=0)


class ChildOutputLocation(SavedOutputModel):
    kind: Literal["child_text"] = "child_text"
    execution_id: str = Field(min_length=1, max_length=80)
    # None selects the checkpoint's final answer, not an activity position.
    activity: int | None = Field(default=None, ge=0)


class SavedOutputTarget(SavedOutputModel):
    producing_thread_id: str = Field(min_length=1, max_length=80)
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    location: Annotated[RootOutputLocation | ChildOutputLocation, Field(discriminator="kind")]


class SavedOutputView(SavedOutputModel):
    target: SavedOutputTarget
    text: str = Field(max_length=64 * 1024)
    offset: int = Field(ge=0)
    total_characters: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class SavedChildOutputPage(SavedOutputModel):
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    outputs: tuple[SavedOutputView, ...]
    total: int = Field(ge=0)
    next_cursor: str | None = None
