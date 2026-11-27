"""Event history never exposes callback secrets or upstream credentials."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue


class ConnectorEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    provider_id: str
    trigger_id: str | None
    source_id: str
    event_type: str
    occurred_at: datetime
    payload: dict[str, JsonValue] | None
    state: Literal["unmatched", "pending", "stored", "ignored", "submitted", "failed"]
    failure: str | None
    attempts: int
    thread_id: str | None
    inbox_id: str | None
    created_at: datetime
    version: int
    retryable: bool = False


class EventPage(BaseModel):
    items: list[ConnectorEvent]
    next_cursor: str | None
