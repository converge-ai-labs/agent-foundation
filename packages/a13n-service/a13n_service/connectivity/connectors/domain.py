"""Public ConnectorProvider and ConnectorConnection resource contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

from a13n_service.connectivity.domain import AdapterKey, DisplayName, JsonObject
from a13n_service.iam.domain import PrincipalRef

from .contracts import ProviderAccess

ConnectorKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9._-]{0,127}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConnectorProviderStatus(StrEnum):
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


class ConnectorProvider(StrictModel):
    id: str
    organization_id: str
    workspace_id: str | None
    name: DisplayName
    type: AdapterKey
    configuration: JsonObject
    status: ConnectorProviderStatus
    version: int = Field(ge=1)
    credential_configured: bool
    credential_generation: int = Field(ge=1)
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class ConnectorProviderCollection(StrictModel):
    items: tuple[ConnectorProvider, ...]
    next_cursor: str | None = None


class ConnectorConnection(StrictModel):
    id: str
    organization_id: str
    workspace_id: str
    connector_provider_id: str
    name: DisplayName
    connector_key: ConnectorKey
    safe_metadata: JsonObject
    status: ConnectorConnectionStatus
    status_reason: ConnectorConnectionStatusReason | None
    version: int = Field(ge=1)
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


class CreateConnectorProviderRequest(StrictModel):
    name: DisplayName
    type: AdapterKey
    configuration: JsonObject
    credentials: dict[str, SecretStr] = Field(
        min_length=1,
        max_length=8,
        repr=False,
        json_schema_extra={"writeOnly": True},
    )


class UpdateConnectorProviderRequest(StrictModel):
    expected_version: int = Field(ge=1)
    name: DisplayName | None = None
    status: ConnectorProviderStatus | None = None
    credentials: dict[str, SecretStr] | None = Field(
        default=None,
        min_length=1,
        max_length=8,
        repr=False,
        json_schema_extra={"writeOnly": True},
    )

    @model_validator(mode="after")
    def validate_change(self) -> UpdateConnectorProviderRequest:
        if "credentials" in self.model_fields_set and self.credentials is None:
            raise ValueError("ConnectorProvider credentials cannot be removed")
        if self.name is None and self.status is None and self.credentials is None:
            raise ValueError("ConnectorProvider update must change at least one field")
        return self


class ReplaceConnectorProviderCredentialsRequest(StrictModel):
    expected_version: int = Field(ge=1)
    credentials: dict[str, SecretStr] = Field(
        min_length=1,
        max_length=8,
        repr=False,
        json_schema_extra={"writeOnly": True},
    )


class ConnectorProviderCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)


class CreateConnectorConnectionRequest(StrictModel):
    connector_provider_id: str = Field(min_length=1, max_length=72)
    name: DisplayName
    connector_key: ConnectorKey


class UpdateConnectorConnectionRequest(StrictModel):
    expected_version: int = Field(ge=1)
    name: DisplayName | None = None

    @model_validator(mode="after")
    def validate_change(self) -> UpdateConnectorConnectionRequest:
        if self.name is None:
            raise ValueError("ConnectorConnection update must change at least one field")
        return self


class ConnectorConnectionCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)


class StartConnectorConnectionSetupRequest(ConnectorConnectionCommandRequest):
    browser_nonce: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$", repr=False)
    setup: JsonObject
    return_path: str = Field(pattern=r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,2047}$")


class ReconnectConnectorConnectionRequest(StartConnectorConnectionSetupRequest):
    pass


class CompleteConnectorSetupRequest(StrictModel):
    attempt_id: str = Field(min_length=1, max_length=72)
    browser_nonce: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    session_uri: str = Field(min_length=1, max_length=4096, repr=False)


class ConnectorSetupCompletion(StrictModel):
    return_path: str


class ConnectorSetupLaunch(StrictModel):
    requires_browser_callback: bool = False
    attempt_id: str
    status: Literal["pending", "completed", "failed", "expired"]
    expires_at: datetime
    connection: ConnectorConnection
    redirect_url: str | None = Field(default=None, max_length=4096, repr=False)


class ConnectorProviderTestResult(StrictModel):
    verified_access: tuple[ProviderAccess, ...]
    connector_provider_id: str
    status: Literal["succeeded"] = "succeeded"
    connector_provider_version: int = Field(ge=1)
    tested_at: datetime


class Connector(StrictModel):
    connector_provider_id: str
    key: ConnectorKey
    name: DisplayName
    description: str | None = Field(default=None, max_length=16_384)
    setup_schema: JsonObject
    authentication_methods: tuple[str, ...] = Field(max_length=32)


class ConnectorCollection(StrictModel):
    items: tuple[Connector, ...] = Field(max_length=2_048)
    next_cursor: None = None
