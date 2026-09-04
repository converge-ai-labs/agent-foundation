"""Application Account management contracts."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from a13n_service.connectivity.domain import AdapterKey, BoundedName, ConfigVersion, JsonObject
from a13n_service.iam.domain import PrincipalRef


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccountStatus(StrEnum):
    active = "active"
    disabled = "disabled"


class Account(StrictModel):
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


class CreateAccountRequest(StrictModel):
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

    @model_validator(mode="after")
    def validate_change(self) -> "UpdateAccountRequest":
        if self.name is None and self.provider_config is None:
            raise ValueError("Account update must change at least one field")
        return self


class AccountCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)


class ReplaceAccountCredentialsRequest(AccountCommandRequest):
    credentials: dict[str, SecretStr] = Field(
        min_length=1, max_length=16, repr=False, json_schema_extra={"writeOnly": True}
    )
