"""Connector subscription commands and public views."""

from datetime import datetime
from typing import Annotated, Literal

from a13n_harness.providers.connector.events import EventType
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class StoreOnly(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["store_only"] = "store_only"


class EventThreadTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["new_thread"] = "new_thread"
    principal_id: str = Field(min_length=1, max_length=72)
    agent_id: str = Field(min_length=1, max_length=72)
    agent_revision_id: str | None = Field(default=None, min_length=1, max_length=72)
    instruction: str = Field(default="Process the following connector event.", min_length=1, max_length=4096)


type Target = Annotated[StoreOnly | EventThreadTarget, Field(discriminator="mode")]


class TriggerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=128)
    connection_id: str = Field(min_length=1, max_length=72)
    event_type: str = Field(min_length=1, max_length=128)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    target: Target = Field(default_factory=StoreOnly)


class TriggerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    target: Target | None = None


class ConnectorTrigger(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    provider_id: str
    workspace_id: str
    connection_id: str
    name: str
    event_type: str
    definition: EventType
    parameters: dict[str, JsonValue]
    target: Target
    state: Literal["enabled", "paused", "retired"]
    remote_id: str | None
    operation_kind: str | None
    failure: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class TriggerPage(BaseModel):
    items: list[ConnectorTrigger]
    next_cursor: str | None


class EventTypePage(BaseModel):
    items: list[EventType]
    next_cursor: None = None
