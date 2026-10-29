"""Managed Browser identities, desired bindings and public observations."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_service.infra.ids import ObjectId

type BrowserStatus = Literal["reserved", "creating", "ready", "deleting", "deleted", "lost"]
type ControlStatus = Literal["automation", "draining", "human", "returning"]


class BrowserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: ObjectId
    name: str = Field(min_length=1, max_length=200)
    recipe: dict[str, JsonValue] = Field(default_factory=dict)
    # Explicit prepare and first use share the same state machine.
    prepare: bool = False
    idle_seconds: int | None = Field(default=1800, ge=60, le=604800)


class BrowserBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)
    browser_id: ObjectId
    tab_access: Literal["global", "thread"] = "thread"
    preparation: Literal["first_use", "run_start"] = "first_use"


class Browser(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str
    provider_id: str
    name: str
    status: BrowserStatus
    control: ControlStatus
    generation: int
    episode: int
    idle_seconds: int | None
    expires_at: datetime | None
    failure: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class BrowserPage(BaseModel):
    items: list[Browser]
    next_cursor: str | None = None


class Takeover(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    thread_id: ObjectId


class Viewer(BaseModel):
    """Sensitive, authorized control response; excluded from ordinary Browser reads."""

    url: str = Field(repr=False)
    expires_at: datetime
    episode: int
