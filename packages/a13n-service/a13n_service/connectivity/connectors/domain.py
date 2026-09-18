"""Public ConnectorProvider and Connection resource contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from a13n_harness.providers.authentication import Authentication
from a13n_harness.providers.connector.contracts import ConnectorKey, ProviderAccess, SetupCompletionMethod
from pydantic import BaseModel, ConfigDict, Field, model_validator

from a13n_service.connectivity.connections.domain import Connection
from a13n_service.connectivity.domain import AdapterKey, DisplayName, JsonObject
from a13n_service.iam.domain import PrincipalRef


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConnectorProviderStatus(StrEnum):
    active = "active"
    disabled = "disabled"


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


class CreateConnectorProviderRequest(StrictModel):
    name: DisplayName
    type: AdapterKey
    configuration: JsonObject
    credentials: JsonObject | None = Field(
        default=None,
        repr=False,
        json_schema_extra={"writeOnly": True},
    )


class UpdateConnectorProviderRequest(StrictModel):
    expected_version: int = Field(ge=1)
    name: DisplayName | None = None
    status: ConnectorProviderStatus | None = None
    credentials: JsonObject | None = Field(
        default=None,
        repr=False,
        json_schema_extra={"writeOnly": True},
    )

    @model_validator(mode="after")
    def validate_change(self) -> UpdateConnectorProviderRequest:
        if self.name is None and self.status is None and "credentials" not in self.model_fields_set:
            raise ValueError("ConnectorProvider update must change at least one field")
        return self


class ReplaceConnectorProviderCredentialsRequest(StrictModel):
    expected_version: int = Field(ge=1)
    credentials: JsonObject | None = Field(
        repr=False,
        json_schema_extra={"writeOnly": True},
    )


class ConnectorProviderCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)


class ConnectorSetupCompletion(StrictModel):
    return_url: str


class ConnectorSetupLaunch(StrictModel):
    completion_method: SetupCompletionMethod
    attempt_id: str
    status: Literal["pending", "completed", "failed", "expired"]
    expires_at: datetime
    connection: Connection
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
    logo_url: str | None = Field(default=None, max_length=2048)
    unavailable_reason: str | None = Field(default=None, max_length=512)
    setup_schema: JsonObject
    authentication_methods: tuple[str, ...] = Field(max_length=32)
    credential_schemas: dict[str, JsonObject] = Field(default_factory=dict)


class ConnectorCollection(StrictModel):
    items: tuple[Connector, ...] = Field(max_length=2_048)
    next_cursor: str | None = None
    refreshed_at: datetime | None = None


class ConnectorProviderMetadata(StrictModel):
    type: str
    display_name: str
    configuration_schema: JsonObject
    credential_schema: JsonObject
    authentication: Authentication
    setup_url: str | None = None
    setup_label: str | None = None


class ConnectorProviderMetadataCollection(StrictModel):
    items: tuple[ConnectorProviderMetadata, ...]
    next_cursor: None = None
