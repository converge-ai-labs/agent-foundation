"""Public Ingress and Route resource contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, StringConstraints, model_validator

from a13n_service.agents.domain import AgentRunOverride
from a13n_service.iam.domain import PrincipalRef

BoundedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
AdapterKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ConfigVersion = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
JsonObject = dict[str, JsonValue]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IngressStatus(StrEnum):
    active = "active"
    disabled = "disabled"


class InputBatchingPolicy(StrictModel):
    min_interval_ms: int = Field(ge=1)
    max_batch_events: int = Field(ge=1)


class Ingress(StrictModel):
    id: str
    organization_id: str
    workspace_id: str
    name: BoundedName
    provider_key: AdapterKey
    provider_config_version: ConfigVersion
    provider_config: JsonObject
    execution_principal_ref: PrincipalRef
    agents: tuple[str, ...] = Field(min_length=1, max_length=128)
    default_agent_id: str
    status: IngressStatus
    version: int = Field(ge=1)
    credential_configured: bool
    credential_generation: int = Field(ge=1)
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class IngressCollection(StrictModel):
    items: tuple[Ingress, ...]
    next_cursor: str | None = None


class CreateIngressRequest(StrictModel):
    name: BoundedName
    provider_key: AdapterKey
    provider_config_version: ConfigVersion
    provider_config: JsonObject
    execution_service_account_id: str
    agents: tuple[str, ...] = Field(min_length=1, max_length=128)
    default_agent_id: str
    credentials: dict[str, SecretStr] = Field(
        min_length=1,
        max_length=16,
        repr=False,
        json_schema_extra={"writeOnly": True},
    )

    @model_validator(mode="after")
    def validate_agent_set(self) -> CreateIngressRequest:
        if len(set(self.agents)) != len(self.agents):
            raise ValueError("agents must be unique")
        if self.default_agent_id not in self.agents:
            raise ValueError("default Agent must occur in agents")
        return self


class UpdateIngressRequest(StrictModel):
    expected_version: int = Field(ge=1)
    name: BoundedName | None = None
    provider_config: JsonObject | None = None
    agents: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=128)
    default_agent_id: str | None = None

    @model_validator(mode="after")
    def validate_change(self) -> UpdateIngressRequest:
        if not (self.model_fields_set - {"expected_version"}):
            raise ValueError("Ingress update must change at least one field")
        if self.agents is not None and len(set(self.agents)) != len(self.agents):
            raise ValueError("agents must be unique")
        if self.agents is not None and self.default_agent_id is not None and self.default_agent_id not in self.agents:
            raise ValueError("default Agent must occur in agents")
        return self


class IngressCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)


class ReplaceIngressCredentialsRequest(IngressCommandRequest):
    credentials: dict[str, SecretStr] = Field(
        min_length=1,
        max_length=16,
        repr=False,
        json_schema_extra={"writeOnly": True},
    )


class Route(StrictModel):
    id: str
    organization_id: str
    workspace_id: str
    ingress_id: str
    name: BoundedName
    provider_config_version: ConfigVersion
    match: JsonObject
    agent_id: str | None = None
    input_mapping: JsonObject | None = None
    input_batching: InputBatchingPolicy
    capability_overlays: dict[str, AgentRunOverride] = Field(default_factory=dict, max_length=128)
    provider_policy: JsonObject
    enabled: bool
    version: int = Field(ge=1)
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class RouteCollection(StrictModel):
    items: tuple[Route, ...]
    next_cursor: str | None = None


class CreateRouteRequest(StrictModel):
    name: BoundedName
    match: JsonObject
    agent_id: str | None = None
    input_mapping: JsonObject | None = None
    input_batching: InputBatchingPolicy
    capability_overlays: dict[str, AgentRunOverride] = Field(default_factory=dict, max_length=128)
    provider_policy: JsonObject
    enabled: bool = True


class UpdateRouteRequest(StrictModel):
    expected_version: int = Field(ge=1)
    name: BoundedName | None = None
    match: JsonObject | None = None
    agent_id: str | None = None
    input_mapping: JsonObject | None = None
    input_batching: InputBatchingPolicy | None = None
    capability_overlays: dict[str, AgentRunOverride] | None = Field(default=None, max_length=128)
    provider_policy: JsonObject | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_change(self) -> UpdateRouteRequest:
        if not (self.model_fields_set - {"expected_version"}):
            raise ValueError("Route update must change at least one field")
        return self
