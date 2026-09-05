"""Fixed, ordered event projection into canonical AgentInput."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, JsonValue

from a13n_service.connectivity.management import canonical_json
from a13n_service.interactions.input import AgentInput


class EventInputView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: str
    occurred_at: datetime | None
    text: str | None
    actor: dict[str, JsonValue] | None
    context: dict[str, JsonValue]
    data: dict[str, JsonValue]


def project_events(values: tuple[dict, ...], *, max_bytes: int) -> AgentInput:
    events = [
        EventInputView.model_validate({key: value.get(key) for key in EventInputView.model_fields}).model_dump(
            mode="json"
        )
        for value in values
    ]
    result = AgentInput.model_validate({"schema_version": "2", "content": [], "structured_content": {"events": events}})
    if len(canonical_json(result.model_dump(mode="json")).encode()) > max_bytes:
        raise ValueError("input_too_large")
    return result
