"""Organization requests compose a target, a model and task instructions."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class FileMemoryTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["memory"] = "memory"
    id: str = Field(min_length=1, max_length=72)


class SpaceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["space"] = "space"
    id: str = Field(min_length=1, max_length=72)


type OrganizationTarget = Annotated[FileMemoryTarget | SpaceTarget, Field(discriminator="kind")]


class OrganizationHistory(BaseModel):
    """Explicit Sessions and a bounded, inclusive seal-time window."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    session_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    after: AwareDatetime | None = None
    before: AwareDatetime | None = None
    max_runs: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def valid_selection(self):
        if len(set(self.session_ids)) != len(self.session_ids):
            raise ValueError("Session IDs must be unique")
        if self.after is not None and self.before is not None and self.after > self.before:
            raise ValueError("after must not be later than before")
        return self


class OrganizationSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: str
    thread_id: str
    run_id: str
    sealed_at: datetime


class OrganizationSourcePage(BaseModel):
    items: list[OrganizationSource]
    next_offset: int | None = None


class OrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: OrganizationTarget
    model: str = Field(min_length=1, max_length=256)
    instructions: str = Field(default="Organize the saved memory files.", min_length=1, max_length=16384)
    history: OrganizationHistory | None = None


class MemoryOrganization(BaseModel):
    id: str
    workspace_id: str
    request: OrganizationRequest
    sources: list[OrganizationSource]
    session_id: str
    thread_id: str
    run_id: str
    created_by_id: str
    created_at: datetime


class MemoryOrganizationPage(BaseModel):
    items: list[MemoryOrganization]
    next_cursor: str | None = None
