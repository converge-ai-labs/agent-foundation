"""Memory subjects, Agent selection, and bounded public representations."""

from typing import Annotated

from a13n_harness.capabilities.mem0 import Mem0Scope
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from a13n_service.interactions.domain import ThreadId

MemoryText = Annotated[str, StringConstraints(min_length=1, max_length=8000, pattern=r"\S")]


class MemorySelection(BaseModel):
    """Opt-in Agent behavior; backend credentials and subject IDs are host-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Mem0Scope | None = None
    auto_recall: bool = True
    toolset: bool = True
    recall_limit: int = Field(default=5, ge=1, le=100)
    recall_threshold: float | None = Field(default=None, ge=0, le=1)
    recall_timeout: float = Field(default=2, gt=0, le=300)
    recall_required: bool = False


class MemoryScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Mem0Scope
    subject_id: ThreadId | None = None

    @model_validator(mode="after")
    def validate_subject(self) -> "MemoryScope":
        if (self.scope is Mem0Scope.USER) != (self.subject_id is None):
            raise ValueError("thread and agent scopes require subject_id; user scope uses the authenticated User")
        return self


class MemoryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: MemoryText


class MemorySearch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    query: Annotated[str, StringConstraints(min_length=1, max_length=16000)]
    limit: int = Field(default=20, ge=1, le=100)
    threshold: float | None = Field(default=None, ge=0, le=1)


class Memory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=512)
    memory: str = Field(min_length=1, max_length=256 * 1024)
    score: float | None = Field(default=None, allow_inf_nan=False)


class MemoryCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[Memory, ...]
    next_cursor: str | None = None
