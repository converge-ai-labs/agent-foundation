"""Providers, versioned recipes, and actual working environments."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from a13n_environment import EnvironmentState
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from a13n_service.ids import ObjectId

EnvironmentName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
JsonObject = dict[str, JsonValue]
Duration = Annotated[int, Field(ge=0, strict=True)]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EnvironmentAccess(StrEnum):
    read_only = "read_only"
    read_write = "read_write"
    full = "full"


class RetentionWindow(DomainModel):
    stop_after: Duration | None
    delete_after: Duration | None

    @model_validator(mode="after")
    def ordered_deadlines(self) -> RetentionWindow:
        if self.stop_after is not None and self.delete_after is not None and self.delete_after <= self.stop_after:
            raise ValueError("delete_after must be later than stop_after")
        return self


class RetentionPolicy(DomainModel):
    idle: RetentionWindow


class EnvironmentProvider(DomainModel):
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId | None
    type: str
    name: EnvironmentName
    configuration: JsonObject
    enabled: bool
    credential_configured: bool
    created_at: datetime
    updated_at: datetime


class EnvironmentProviderDefinition(DomainModel):
    type: str
    display_name: str
    configuration_versions: tuple[str, ...]
    configuration_schema: JsonObject
    credential_schema: JsonObject | None
    supports_managed: bool
    supports_stop: bool
    supports_destroy: bool
    requires_keepalive: bool


class EnvironmentConfiguration(DomainModel):
    configuration_schema_version: str = "1"
    configuration: JsonObject


class TemplateConfiguration(EnvironmentConfiguration):
    provider_id: ObjectId
    access: EnvironmentAccess = EnvironmentAccess.full
    preparation: Literal["on_run", "on_use"] = "on_run"
    retention: RetentionPolicy


class EnvironmentTemplate(DomainModel):
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId | None
    name: EnvironmentName
    description: str | None
    version: int
    current_revision_id: ObjectId
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EnvironmentTemplateRevision(TemplateConfiguration):
    id: ObjectId
    template_id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId | None
    version: int
    created_at: datetime


class EnvironmentStatus(StrEnum):
    unprepared = "unprepared"
    running = "running"
    stopped = "stopped"
    deleted = "deleted"
    unavailable = "unavailable"


class Environment(DomainModel):
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    provider_id: ObjectId
    template_revision_id: ObjectId | None
    ownership: Literal["managed", "external"]
    access: EnvironmentAccess
    generation: int
    status: EnvironmentStatus
    retention_condition: Literal["active", "idle"]
    condition_since: datetime
    created_at: datetime
    updated_at: datetime


class ExistingEnvironmentSelection(DomainModel):
    environment_id: ObjectId


class NewEnvironmentSelection(DomainModel):
    template_id: ObjectId
    version: Annotated[int, Field(ge=1)] | None = None


type EnvironmentSelection = ExistingEnvironmentSelection | NewEnvironmentSelection


class CreateProviderRequest(DomainModel):
    type: str
    name: EnvironmentName
    configuration: JsonObject = Field(default_factory=dict)
    credential: JsonObject | None = Field(default=None, repr=False, exclude=True)


class UpdateProviderRequest(DomainModel):
    name: EnvironmentName | None = None
    enabled: bool | None = None
    credential: JsonObject | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})


class ReplaceCredentialRequest(DomainModel):
    credential: JsonObject | None = Field(repr=False)


class CreateTemplateRequest(TemplateConfiguration):
    name: EnvironmentName
    description: Annotated[str, Field(max_length=4096)] | None = None


class CreateTemplateRevisionRequest(TemplateConfiguration):
    expected_version: Annotated[int, Field(ge=1)]


class UpdateTemplateRequest(DomainModel):
    name: EnvironmentName | None = None
    description: Annotated[str, Field(max_length=4096)] | None = None
    archived: bool | None = None


class RegisterEnvironmentRequest(EnvironmentConfiguration):
    provider_id: ObjectId
    state: EnvironmentState | None = None
    access: EnvironmentAccess = EnvironmentAccess.full


type CreateEnvironmentRequest = NewEnvironmentSelection | RegisterEnvironmentRequest


class Collection[T](DomainModel):
    items: tuple[T, ...]
    next_cursor: str | None = None


def retention_action(
    policy: RetentionPolicy,
    *,
    condition: Literal["active", "idle"],
    since: datetime,
    status: EnvironmentStatus,
    now: datetime,
) -> Literal["stop", "delete"] | None:
    """Choose a due action from condition time; stopping never resets this clock."""
    if condition == "active" or status in {EnvironmentStatus.unprepared, EnvironmentStatus.deleted}:
        return None
    window = policy.idle
    if window.delete_after is not None and now >= since + timedelta(seconds=window.delete_after):
        return "delete"
    if (
        status != EnvironmentStatus.stopped
        and window.stop_after is not None
        and now >= since + timedelta(seconds=window.stop_after)
    ):
        return "stop"
    return None


class EnvironmentCommand(DomainModel):
    id: ObjectId
    environment_id: ObjectId
    action: Literal["stop", "delete"]
    status: Literal["pending", "completed", "failed"]
    created_at: datetime
    completed_at: datetime | None


class EnvironmentCommandRequest(DomainModel):
    action: Literal["stop", "delete"]
