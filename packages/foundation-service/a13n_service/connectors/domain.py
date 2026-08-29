"""Typed Connector product resources and mutation inputs."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .errors import ConnectorError

CONNECTOR_ID_PREFIX = "con"
CONNECTOR_REVISION_ID_PREFIX = "conrev"
CONNECTION_ID_PREFIX = "conn"
CONNECTION_SETUP_ID_PREFIX = "consetup"
TRIGGER_ID_PREFIX = "trg"
TRIGGER_OCCURRENCE_ID_PREFIX = "tocc"

Name = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(max_length=4_000)]
ProviderKey = Annotated[str, Field(min_length=1, max_length=200)]
ProviderVersion = Annotated[str, Field(min_length=1, max_length=200)]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PrincipalRef(DomainModel):
    principal_type: Literal["user", "service_account"]
    principal_id: Annotated[str, Field(min_length=1, max_length=64)]


class Connector(DomainModel):
    id: str
    organization_id: str
    workspace_id: str
    name: str
    description: str | None
    enabled: bool
    version: int
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class ConnectorRevision(DomainModel):
    id: str
    organization_id: str
    workspace_id: str
    connector_id: str
    version: int
    provider_key: str
    provider_config_version: str
    config: dict[str, JsonValue]
    created_by: PrincipalRef
    created_at: datetime


class CreateConnector(DomainModel):
    organization_id: Annotated[str, Field(min_length=1, max_length=64)]
    workspace_id: Annotated[str, Field(min_length=1, max_length=64)]
    name: Name
    description: Description | None = None
    enabled: bool = True
    provider_key: ProviderKey
    provider_config_version: ProviderVersion
    config: dict[str, JsonValue]
    created_by: PrincipalRef


class UpdateConnector(DomainModel):
    name: Name | None = None
    description: Description | None = None
    enabled: bool | None = None
    expected_version: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def require_change(self) -> UpdateConnector:
        if not self.model_fields_set.intersection({"name", "description", "enabled"}):
            raise ValueError("at least one Connector field must be supplied")
        return self


class CreateConnectorRevision(DomainModel):
    provider_key: ProviderKey
    provider_config_version: ProviderVersion
    config: dict[str, JsonValue]
    created_by: PrincipalRef


class ConnectorCreateResult(DomainModel):
    connector: Connector
    revision: ConnectorRevision


class ConnectorRevisionCreateResult(DomainModel):
    revision: ConnectorRevision
    created: bool


class FrozenConnectorTool(DomainModel):
    provider_tool_name: Annotated[str, Field(min_length=1, max_length=200)]
    model_tool_name: Annotated[str, Field(min_length=1, max_length=256)]
    tool_id: Annotated[str, Field(min_length=1, max_length=256)]
    description: Annotated[str, Field(max_length=4_000)]
    parameters_json_schema: dict[str, JsonValue]
    effects: tuple[Literal["read", "write", "delete", "execute", "external_communication"], ...] = ()
    credential_audiences: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] = ()
    idempotency: Literal["none", "read_only", "provider_key"] = "none"
    output_policy: dict[str, JsonValue]


class ConnectorProviderDependencyLock(DomainModel):
    provider_key: str
    distribution_name: str
    distribution_version: str
    class_module: str
    class_qualname: str


class AgentConnectorDeclaration(DomainModel):
    connector_revision_id: str
    connection_id: str | None
    tools: tuple[FrozenConnectorTool, ...]
    provider_lock: ConnectorProviderDependencyLock


class ConnectorTurnSelection(DomainModel):
    declaration_index: Annotated[int, Field(ge=0)]
    connector_revision_id: str
    connection_id: str | None


class AcceptedTriggerSource(DomainModel):
    trigger_id: str
    trigger_version: Annotated[int, Field(ge=1)]
    source_kind: Literal["schedule", "connector_event"]
    occurrence_key: Annotated[str, Field(min_length=1, max_length=500)]


class TriggerOccurrenceReceipt(DomainModel):
    trigger_id: str
    occurrence_key: str
    turn_id: str
    duplicate: bool


class ConnectionStatus(StrEnum):
    active = "active"
    disabled = "disabled"
    reauthorization_required = "reauthorization_required"
    revoked = "revoked"


class ConnectionAccount(DomainModel):
    external_id: str
    display_name: str


class Connection(DomainModel):
    id: str
    organization_id: str
    workspace_id: str
    connector_id: str
    principal_ref: PrincipalRef | None
    name: str
    provider_key: str
    account: ConnectionAccount
    status: ConnectionStatus
    expires_at: datetime | None
    version: int
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class StartConnectionSetup(DomainModel):
    organization_id: Annotated[str, Field(min_length=1, max_length=64)]
    workspace_id: Annotated[str, Field(min_length=1, max_length=64)]
    connector_revision_id: Annotated[str, Field(min_length=1, max_length=64)]
    connection_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    principal_ref: PrincipalRef | None
    name: Name
    setup_mode: Annotated[str, Field(min_length=1, max_length=100)]
    input: dict[str, JsonValue]
    actor: PrincipalRef


class ConnectionSetupReceipt(DomainModel):
    setup_id: str
    provider_key: str
    expires_at: datetime
    completed: bool
    next_action: dict[str, JsonValue] | None
    connection: Connection | None


class UpdateConnection(DomainModel):
    name: Name | None = None
    expected_version: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def require_change(self) -> UpdateConnection:
        if "name" not in self.model_fields_set:
            raise ValueError("name must be supplied")
        return self


class TriggerStatus(StrEnum):
    disabled = "disabled"
    activating = "activating"
    active = "active"
    failed = "failed"


class ScheduleTriggerSource(DomainModel):
    kind: Literal["schedule"] = "schedule"
    type: Literal["cron", "interval"]
    expression: str | None = None
    timezone: str | None = None
    interval_seconds: int | None = None

    @model_validator(mode="after")
    def validate_schedule(self) -> ScheduleTriggerSource:
        if self.type == "cron":
            if self.expression is None or self.timezone is None or self.interval_seconds is not None:
                raise ValueError("cron schedules require expression and timezone only")
            if len(self.expression.split()) != 5:
                raise ValueError("cron expression must contain exactly five fields")
        elif self.interval_seconds is None or self.interval_seconds <= 0 or self.expression or self.timezone:
            raise ValueError("interval schedules require only a positive interval_seconds")
        return self


class ConnectorEventTriggerSource(DomainModel):
    kind: Literal["connector_event"] = "connector_event"
    connector_revision_id: str
    connection_id: str
    event_type: Annotated[str, Field(min_length=1, max_length=200)]
    provider_event_config_version: ProviderVersion
    config: dict[str, JsonValue]


TriggerSource = Annotated[ScheduleTriggerSource | ConnectorEventTriggerSource, Field(discriminator="kind")]


class Trigger(DomainModel):
    id: str
    organization_id: str
    workspace_id: str
    name: str
    description: str | None
    principal_ref: PrincipalRef
    agent_revision_id: str
    source: TriggerSource
    input_template: dict[str, JsonValue]
    status: TriggerStatus
    status_reason: str | None
    version: int
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class CreateTrigger(DomainModel):
    organization_id: Annotated[str, Field(min_length=1, max_length=64)]
    workspace_id: Annotated[str, Field(min_length=1, max_length=64)]
    name: Name
    description: Description | None = None
    principal_ref: PrincipalRef
    agent_revision_id: Annotated[str, Field(min_length=1, max_length=64)]
    source: TriggerSource
    input_template: dict[str, JsonValue]
    created_by: PrincipalRef


class UpdateTrigger(DomainModel):
    name: Name | None = None
    description: Description | None = None
    principal_ref: PrincipalRef | None = None
    agent_revision_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    source: TriggerSource | None = None
    input_template: dict[str, JsonValue] | None = None
    expected_version: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def require_change(self) -> UpdateTrigger:
        if not self.model_fields_set.intersection(
            {"name", "description", "principal_ref", "agent_revision_id", "source", "input_template"}
        ):
            raise ValueError("at least one Trigger field must be supplied")
        return self

    @property
    def changes_behavior(self) -> bool:
        return bool(
            self.model_fields_set.intersection({"principal_ref", "agent_revision_id", "source", "input_template"})
        )


def bounded_json_object(value: dict[str, JsonValue], *, field_name: str) -> dict[str, JsonValue]:
    """Detach and enforce the common Connector JSON boundary."""

    try:
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        raise ConnectorError(
            f"{field_name} must be bounded finite JSON.",
            code="invalid_request",
            details={"field": field_name},
        ) from None
    if not isinstance(decoded, dict) or _json_depth(decoded) > 16 or _json_members(decoded) > 4_096:
        raise ConnectorError(
            f"{field_name} must be a bounded JSON object.",
            code="invalid_request",
            details={"field": field_name},
        )
    return decoded


def bounded_json_value(value: JsonValue, *, field_name: str) -> JsonValue:
    """Detach and enforce the common Connector JSON boundary for any JSON value."""

    try:
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError
        decoded: JsonValue = json.loads(encoded)
    except (TypeError, ValueError):
        raise ConnectorError(
            f"{field_name} must be bounded finite JSON.",
            code="invalid_request",
            details={"field": field_name},
        ) from None
    if _json_depth(decoded) > 16 or _json_members(decoded) > 4_096:
        raise ConnectorError(
            f"{field_name} must be bounded JSON.",
            code="invalid_request",
            details={"field": field_name},
        )
    return decoded


def _json_depth(value: JsonValue) -> int:
    if isinstance(value, dict):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 1


def _json_members(value: JsonValue) -> int:
    if isinstance(value, dict):
        return len(value) + sum(_json_members(item) for item in value.values())
    if isinstance(value, list):
        return len(value) + sum(_json_members(item) for item in value)
    return 0
