"""Application Account management contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from a13n_service.connectivity.domain import AdapterKey, ConfigVersion, DisplayName, JsonObject
from a13n_service.iam.domain import PrincipalRef

from .reception import InputBatchingPolicy, Reception, ReceptionScope


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccountProviderDefinition(StrictModel):
    provider_key: AdapterKey
    config_version: ConfigVersion
    configuration_schema: JsonObject
    credential_schema: JsonObject
    reception_policy_schema: JsonObject
    target_kinds: tuple[Literal["conversation", "repository"], ...]


class AccountProviderDefinitionCollection(StrictModel):
    items: tuple[AccountProviderDefinition, ...]


class AccountStatus(StrEnum):
    active = "active"
    disabled = "disabled"


class Account(Reception):
    id: str
    organization_id: str
    workspace_id: str
    name: DisplayName
    provider_key: AdapterKey
    provider_config_version: ConfigVersion
    provider_config: JsonObject
    status: AccountStatus
    version: int = Field(ge=1)
    credential_configured: bool
    credential_generation: int = Field(ge=1)
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class AccountCollection(StrictModel):
    items: tuple[Account, ...]
    next_cursor: str | None = None


class CreateAccountRequest(Reception):
    name: DisplayName
    provider_key: AdapterKey
    provider_config_version: ConfigVersion
    provider_config: JsonObject
    credentials: dict[str, SecretStr] = Field(
        min_length=1, max_length=16, repr=False, json_schema_extra={"writeOnly": True}
    )


class UpdateAccountRequest(StrictModel):
    expected_version: int = Field(ge=1)
    name: DisplayName | None = None
    provider_config: JsonObject | None = None
    reception_scope: ReceptionScope | None = None
    receive_enabled: bool | None = None
    default_agent_id: str | None = None
    execution_service_account_id: str | None = None
    input_batching: InputBatchingPolicy | None = None
    provider_policy: JsonObject | None = None

    @model_validator(mode="after")
    def validate_change(self) -> "UpdateAccountRequest":
        if not (self.model_fields_set - {"expected_version"}):
            raise ValueError("Account update must change at least one field")
        return self


class AccountCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)


class ReplaceAccountCredentialsRequest(AccountCommandRequest):
    credentials: dict[str, SecretStr] = Field(
        min_length=1, max_length=16, repr=False, json_schema_extra={"writeOnly": True}
    )


class EventConnectionStatus(StrictModel):
    transport: Literal["http", "websocket"]
    state: Literal["http", "disabled", "connecting", "connected", "reconnecting", "disconnected"]
    observed_at: datetime | None = None
    last_event_at: datetime | None = None
    error_code: str | None = None
