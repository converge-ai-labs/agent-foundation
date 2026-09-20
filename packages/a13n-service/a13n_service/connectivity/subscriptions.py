"""Trusted provider contracts for discoverable, validated event subscriptions."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from .domain import JsonObject
from .ingress.provider import InboundEvent


class EventTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_target_id: str = Field(min_length=1, max_length=72)
    event_type: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.]+$")
    filters: dict[str, StrictStr | StrictInt | StrictBool | None] = Field(default_factory=dict, max_length=16)
    once: bool = Field(default=True, strict=True)

    def describe(self) -> str:
        conditions = ", ".join(f"{key}: {value}" for key, value in self.filters.items())
        return (
            f"{self.event_type} · {conditions or 'all matching events'} · {'notify once' if self.once else 'ongoing'}"
        )


@dataclass(frozen=True)
class EventType:
    key: str
    description: str
    filters: type[BaseModel]

    def validate(self, trigger: EventTrigger) -> BaseModel:
        return self.filters.model_validate(trigger.filters, strict=True)

    def describe(self) -> JsonObject:
        return {
            "event_type": self.key,
            "description": self.description,
            "filter_schema": self.filters.model_json_schema(),
        }


@dataclass(frozen=True)
class MatchedEvent:
    """Bounded facts only; adapters must never forward raw bodies or untrusted URLs."""

    key: str
    external_target_id: str
    occurred_at: datetime
    facts: JsonObject
    timestamp_precision_seconds: bool = False


class EventSubscriptions(Protocol):
    config_versions: frozenset[str]
    target_kind: str
    event_types: tuple[EventType, ...]
    setup: str

    def match(
        self, trigger: EventTrigger, event: InboundEvent, *, configuration: JsonObject, policy: JsonObject
    ) -> MatchedEvent | None: ...


def require_event_type(adapter: EventSubscriptions, trigger: EventTrigger) -> EventType:
    for event_type in adapter.event_types:
        if event_type.key == trigger.event_type:
            event_type.validate(trigger)
            return event_type
    raise ValueError("event_type_unavailable")
