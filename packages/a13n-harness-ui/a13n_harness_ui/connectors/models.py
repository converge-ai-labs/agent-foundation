"""Safe Connector declarations, setup interactions and immutable Run bindings."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_harness_ui.connector_models import ConnectorRule


class Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EventDeclaration(Value):
    key: str = Field(min_length=1, max_length=128)
    name: str
    description: str = ""
    conditions_schema: dict[str, JsonValue] | None = None


class Declaration(Value):
    api_version: Literal[1] = 1
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    name: str
    description: str
    configuration_schema: dict[str, JsonValue]
    events: tuple[EventDeclaration, ...] = ()
    bot_delivery: bool = False
    setup_intents: tuple[str, ...] = ("configure",)


class CatalogItem(Value):
    key: str
    declaration: Declaration | None = None
    implementation: str = ""
    available: bool = True
    diagnostic: str | None = None


class Status(Value):
    state: Literal["unconfigured", "ready", "disabled", "degraded", "plugin_unavailable", "runtime_unavailable"]
    summary: str


class Instance(Value):
    connector_id: str
    kind: str
    name: str
    version: int
    enabled: bool
    deliver_to_bot: bool
    settings: dict[str, JsonValue]
    rules: tuple[ConnectorRule, ...] = ()
    configured_secrets: tuple[str, ...] = ()
    status: Status


class SetupAction(Value):
    id: str
    label: str
    kind: Literal["submit", "open_url", "copy", "confirm"] = "submit"
    url: str | None = None
    value: str | None = None


class Wait(Value):
    message: str
    poll_after_ms: int = Field(ge=250, le=60_000)
    expires_at: datetime


class Step(Value):
    key: str
    title: str
    description: str = ""
    state: Literal["input", "working", "waiting", "completed", "failed", "cancelled", "expired"] = "input"
    form: dict[str, JsonValue] | None = None
    values: dict[str, JsonValue] = Field(default_factory=dict)
    actions: tuple[SetupAction, ...] = ()
    wait: Wait | None = None
    error: str | None = None


class SetupSnapshot(Value):
    session_id: str
    connector_id: str
    version: int
    expires_at: datetime
    step: Step


class SetupCommand(Value):
    operation_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    action: str
    values: dict[str, JsonValue] = Field(default_factory=dict, exclude=True, repr=False)


class SetupOffer(Value):
    offer_id: str
    thread_id: str
    connector_key: str
    connector_id: str | None = None
    text: str = ""
    tool_call_id: str | None = None
    run_id: str | None = None
    created_at: datetime


class Capture(Value):
    connector_id: str
    connector_key: str
    configuration_version: int
    implementation: str
    # Plugin-authored safe recipe. Private credentials and state are never copied here.
    binding: dict[str, JsonValue] = Field(default_factory=dict)
