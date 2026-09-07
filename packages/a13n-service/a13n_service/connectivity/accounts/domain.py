"""Application Account management contracts."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from a13n_service.connectivity.domain import AdapterKey, BoundedName, ConfigVersion, JsonObject
from a13n_service.iam.domain import PrincipalRef

from .reception import InputBatchingPolicy, Reception


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccountStatus(StrEnum):
    active = "active"
    disabled = "disabled"


class Account(Reception):
    id: str
    organization_id: str
    workspace_id: str
    name: BoundedName
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
    name: BoundedName
    provider_key: AdapterKey
    provider_config_version: ConfigVersion
    provider_config: JsonObject
    credentials: dict[str, SecretStr] = Field(
        min_length=1, max_length=16, repr=False, json_schema_extra={"writeOnly": True}
    )


class UpdateAccountRequest(StrictModel):
    expected_version: int = Field(ge=1)
    name: BoundedName | None = None
    provider_config: JsonObject | None = None
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
