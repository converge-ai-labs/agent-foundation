"""Connector instance configuration and selected, model-visible input envelopes."""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ConnectorModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ConnectorRule(ConnectorModel):
    id: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    event: str = Field(min_length=1, max_length=128)
    conditions: dict[str, JsonValue] = Field(default_factory=dict)


class ConnectorState(ConnectorModel):
    key: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    value: JsonValue


class StateChange(ConnectorModel):
    key: str = Field(min_length=1, max_length=128)
    expected_version: int | None = Field(default=None, ge=1)
    value: JsonValue = None
    delete: bool = False


class ConnectorConfiguration(ConnectorModel):
    """Host-private settings; ordinary serialization exposes only safe identity and policy."""

    connector_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=128)
    version: int = Field(default=1, ge=1)
    credential_version: int = Field(default=1, ge=1)
    rules: tuple["ConnectorRule", ...] = ()
    enabled: bool = True
    deliver_to_bot: bool = False
    selected_events: tuple[str, ...] = Field(default=(), max_length=128)
    settings: dict[str, JsonValue] = Field(default_factory=dict, exclude=True, repr=False)
    credentials: dict[str, str] = Field(default_factory=dict, exclude=True, repr=False)


class ConnectorInput(ConnectorModel):
    """An adapter-authored event, already normalized within its resource scope.

    Text is external content, never human authorization. Credentials and private adapter state
    must not be included. A stable source event ID deduplicates only while pending in the inbox.
    """

    input_id: str = Field(default_factory=lambda: f"input-{uuid4().hex}", min_length=1, max_length=128)
    connector_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=128)
    source_event_id: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1, max_length=16 * 1024)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
