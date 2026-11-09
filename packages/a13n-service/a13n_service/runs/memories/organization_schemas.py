"""Organization requests compose a target, a model and task instructions."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class FileMemoryTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["memory"] = "memory"
    id: str = Field(min_length=1, max_length=72)


class SpaceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["space"] = "space"
    id: str = Field(min_length=1, max_length=72)


type OrganizationTarget = Annotated[FileMemoryTarget | SpaceTarget, Field(discriminator="kind")]


class OrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: OrganizationTarget
    model: str = Field(min_length=1, max_length=256)
    instructions: str = Field(default="Organize the saved memory files.", min_length=1, max_length=16384)


class MemoryOrganization(BaseModel):
    id: str
    workspace_id: str
    request: OrganizationRequest
    session_id: str
    thread_id: str
    run_id: str
    created_by_id: str
    created_at: datetime


class MemoryOrganizationPage(BaseModel):
    items: list[MemoryOrganization]
    next_cursor: str | None = None
