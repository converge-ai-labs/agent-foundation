"""Validation and expansion for the deliberately small Trigger template language."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Literal

from pydantic import JsonValue

from .domain import bounded_json_object
from .errors import ConnectorError
from .provider import ConnectorProviderEvent

type TriggerSourceKind = Literal["schedule", "connector_event"]

_EVENT_PLACEHOLDERS = {
    "{{ event }}",
    "{{ event.data }}",
    "{{ event.type }}",
    "{{ event.occurred_at }}",
}
_SCHEDULE_PLACEHOLDERS = {"{{ scheduled_at }}"}


def validate_trigger_input_template(
    value: dict[str, JsonValue],
    *,
    source_kind: TriggerSourceKind,
) -> dict[str, JsonValue]:
    """Detach a template and reject unsupported expression-like strings."""

    template = bounded_json_object(value, field_name="input_template")
    allowed = _EVENT_PLACEHOLDERS if source_kind == "connector_event" else _SCHEDULE_PLACEHOLDERS
    _validate_placeholders(template, allowed)
    return template


def expand_event_template(
    template: dict[str, JsonValue],
    *,
    event: ConnectorProviderEvent,
    received_at: datetime,
) -> dict[str, JsonValue]:
    event_value: dict[str, JsonValue] = {
        "id": event.event_id,
        "type": event.event_type,
        "data": deepcopy(dict(event.data)),
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at is not None else None,
        "received_at": received_at.isoformat(),
    }
    replacements: dict[str, JsonValue] = {
        "{{ event }}": event_value,
        "{{ event.data }}": event_value["data"],
        "{{ event.type }}": event.event_type,
        "{{ event.occurred_at }}": event_value["occurred_at"],
    }
    return _expand(template, replacements)


def expand_schedule_template(
    template: dict[str, JsonValue],
    *,
    scheduled_at: datetime,
) -> dict[str, JsonValue]:
    return _expand(template, {"{{ scheduled_at }}": scheduled_at.isoformat()})


def _validate_placeholders(value: JsonValue, allowed: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _validate_placeholders(item, allowed)
        return
    if isinstance(value, list):
        for item in value:
            _validate_placeholders(item, allowed)
        return
    if isinstance(value, str) and ("{{" in value or "}}" in value) and value not in allowed:
        raise ConnectorError(
            "Trigger input contains an unsupported placeholder.",
            code="invalid_request",
            details={"field": "input_template"},
        )


def _expand(value: dict[str, JsonValue], replacements: dict[str, JsonValue]) -> dict[str, JsonValue]:
    def replace(item: JsonValue) -> JsonValue:
        if isinstance(item, dict):
            return {key: replace(child) for key, child in item.items()}
        if isinstance(item, list):
            return [replace(child) for child in item]
        if isinstance(item, str) and item in replacements:
            return deepcopy(replacements[item])
        return item

    expanded = replace(value)
    if not isinstance(expanded, dict):
        raise AssertionError("Trigger input template root must remain an object")
    return bounded_json_object(expanded, field_name="input")
