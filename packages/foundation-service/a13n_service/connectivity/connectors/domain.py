"""Public Connector and ConnectorConnection resource contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

from a13n_service.connectivity.ingress.domain import AdapterKey, BoundedName, ConfigVersion, JsonObject
from a13n_service.iam.domain import PrincipalRef

Endpoint = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
ProviderKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9._-]{0,127}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConnectorStatus(StrEnum):
    active = "active"
    disabled = "disabled"


class ConnectorConnectionStatus(StrEnum):
    pending = "pending"
    ready = "ready"
    action_required = "action_required"
    disabled = "disabled"


class ConnectorConnectionStatusReason(StrEnum):
    reauthorization_required = "reauthorization_required"
    incompatible = "incompatible"


class Connector(StrictModel):
    id: str
    organization_id: str
    workspace_id: str
    name: BoundedName
    driver_key: AdapterKey
    config_version: ConfigVersion
    endpoint: Endpoint
    config: JsonObject
    status: ConnectorStatus
    version: int = Field(ge=1)
    credential_configured: bool
    credential_generation: int = Field(ge=1)
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class ConnectorCollection(StrictModel):
    items: tuple[Connector, ...]
    next_cursor: str | None = None


class ConnectorConnection(StrictModel):
    id: str
    organization_id: str
    workspace_id: str
    connector_id: str
    owner_principal_ref: PrincipalRef | None
    name: BoundedName
    provider_key: ProviderKey
    safe_metadata: JsonObject
    status: ConnectorConnectionStatus
    status_reason: ConnectorConnectionStatusReason | None
    version: int = Field(ge=1)
    catalog_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def valid_status_reason(self) -> ConnectorConnection:
        if (self.status is ConnectorConnectionStatus.action_required) != (self.status_reason is not None):
            raise ValueError("status_reason is required exactly for action_required")
        return self


class ConnectorConnectionCollection(StrictModel):
    items: tuple[ConnectorConnection, ...]
    next_cursor: str | None = None


class CreateConnectorRequest(StrictModel):
    name: BoundedName
    driver_key: AdapterKey
    config_version: ConfigVersion
    endpoint: Endpoint
    config: JsonObject
    credentials: dict[str, SecretStr] = Field(
        min_length=1,
        max_length=8,
        repr=False,
        json_schema_extra={"writeOnly": True},
    )


class UpdateConnectorRequest(StrictModel):
    expected_version: int = Field(ge=1)
    name: BoundedName | None = None

    @model_validator(mode="after")
    def validate_change(self) -> UpdateConnectorRequest:
        if self.name is None:
            raise ValueError("Connector update must change at least one field")
        return self


class ReplaceConnectorCredentialsRequest(StrictModel):
    expected_version: int = Field(ge=1)
    credentials: dict[str, SecretStr] = Field(
        min_length=1,
        max_length=8,
        repr=False,
        json_schema_extra={"writeOnly": True},
    )


class ConnectorCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)


class CreateConnectorConnectionRequest(StrictModel):
    name: BoundedName
    provider_key: ProviderKey
    owner_principal_ref: PrincipalRef | None = None
    setup: JsonObject
    return_path: str = Field(pattern=r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,2047}$")


class UpdateConnectorConnectionRequest(StrictModel):
    expected_version: int = Field(ge=1)
    name: BoundedName | None = None

    @model_validator(mode="after")
    def validate_change(self) -> UpdateConnectorConnectionRequest:
        if self.name is None:
            raise ValueError("ConnectorConnection update must change at least one field")
        return self


class ConnectorConnectionCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)


class ConnectorSetupLaunch(StrictModel):
    connection: ConnectorConnection
    redirect_url: str | None = Field(default=None, max_length=4096, repr=False)
