"""Optional connector event capability; hosts own persistence, authorization and delivery."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, SecretStr


class EventType(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str = Field(min_length=1, max_length=128)
    app: str = Field(min_length=1, max_length=128)
    name: str = Field(max_length=256)
    description: str = Field(default="", max_length=16384)
    instructions: str = Field(default="", max_length=16384)
    version: str | None = None
    config_schema: dict[str, JsonValue]
    payload_schema: dict[str, JsonValue] | None = None
    requires_provider_setup: bool = False


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_id: str = Field(min_length=1, max_length=256)
    instance_id: str = Field(min_length=1, max_length=256)
    account_id: str = Field(min_length=1, max_length=256)
    user_id: str = Field(min_length=1, max_length=256)
    event_type: str = Field(min_length=1, max_length=128)
    occurred_at: AwareDatetime
    payload: dict[str, JsonValue]


class EventWebhook(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    # An upstream-authenticated, stable identity for its shared delivery configuration.
    id: str = Field(min_length=1, max_length=256)
    url: str
    secret: SecretStr


class EventInstance(BaseModel):
    id: str
    account_id: str
    user_id: str
    event_type: str
    config: dict[str, JsonValue]
    disabled: bool
    version: str
    destination: str | None = None


class EventProvider(Protocol):
    async def event_types(self, app: str) -> list[EventType]: ...
    async def event_type(self, app: str, key: str) -> EventType: ...
    async def webhook(self) -> EventWebhook | None: ...
    async def configure_webhook(self, url: str, existing_id: str | None, *, rotate: bool) -> EventWebhook: ...
    async def instances(self, account_id: str, event_type: str) -> list[EventInstance]: ...
    async def upsert(
        self, definition: EventType, account_id: str, user_id: str, config: dict[str, JsonValue]
    ) -> str: ...
    async def transition(self, instance_id: str, state: str) -> None: ...


@runtime_checkable
class HasEvents(Protocol):
    @property
    def events(self) -> EventProvider: ...


class VerifyEvent(Protocol):
    def __call__(self, body: bytes, headers: Mapping[str, str], secret: str, *, now: datetime) -> Event: ...
