"""API contracts for connection management and delegated authorization."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from a13n_service.connectivity.browser_urls import split_browser_url
from a13n_service.connectivity.domain import JsonObject
from a13n_service.iam.domain import PrincipalRef
from a13n_service.names import DisplayName


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConnectorSource(StrictModel):
    kind: Literal["connector"]
    provider_id: str = Field(min_length=1, max_length=72)
    connector_key: str = Field(min_length=1, max_length=128)


class MCPSource(StrictModel):
    kind: Literal["mcp"]
    endpoint_url: str = Field(min_length=1, max_length=2048)
    auth_mode: Literal["none", "bearer", "oauth", "static_headers"]
    static_header_names: tuple[str, ...] = Field(default=(), max_length=16)


ConnectionSource = Annotated[ConnectorSource | MCPSource, Field(discriminator="kind")]


class ConnectionStatus(StrEnum):
    pending = "pending"
    ready = "ready"
    action_required = "action_required"
    disabled = "disabled"


class ConnectionStatusReason(StrEnum):
    reauthorization_required = "reauthorization_required"
    incompatible = "incompatible"


class ConnectionCheck(StrictModel):
    checked_at: datetime
    scope: Literal["provider_account", "mcp_discovery"]
    status: Literal["passed", "action_required", "unavailable"]
    error_code: str | None = None


class Connection(StrictModel):
    id: str
    organization_id: str
    workspace_id: str
    name: DisplayName
    source: ConnectionSource
    status: ConnectionStatus
    status_reason: ConnectionStatusReason | None = None
    version: int = Field(ge=1)
    authorization_generation: int = Field(ge=1)
    credential_configured: bool
    safe_metadata: JsonObject = Field(default_factory=dict)
    last_check: ConnectionCheck | None = None
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def valid_status_reason(self) -> Connection:
        if (self.status is ConnectionStatus.action_required) != (self.status_reason is not None):
            raise ValueError("status_reason is required exactly for action_required")
        return self


class ConnectionCollection(StrictModel):
    items: tuple[Connection, ...]
    next_cursor: str | None = None


class CreateConnectionRequest(StrictModel):
    name: DisplayName
    source: ConnectionSource


class ConnectionCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)


class UpdateConnectionRequest(ConnectionCommandRequest):
    name: DisplayName


class CreateAuthorizationRequest(ConnectionCommandRequest):
    method: Literal["browser", "credentials", "client_credentials"]
    options: JsonObject = Field(default_factory=dict)
    credentials: dict[str, SecretStr] | None = Field(
        default=None, min_length=1, max_length=16, repr=False, json_schema_extra={"writeOnly": True}
    )
    return_url: str | None = Field(default=None, min_length=1, max_length=2048)
    state: str | None = Field(default=None, min_length=32, max_length=512, repr=False)
    completion_challenge: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$", repr=False)

    @model_validator(mode="after")
    def validate_method(self) -> CreateAuthorizationRequest:
        if self.method == "browser":
            if (
                self.return_url is None
                or self.state is None
                or self.completion_challenge is None
                or self.credentials is not None
            ):
                raise ValueError("Browser authorization requires a return URL, state, and completion challenge")
            try:
                split_browser_url(self.return_url)
            except ValueError as error:
                raise ValueError(
                    "Authorization return URL must use HTTPS or exact loopback HTTP without credentials or a fragment"
                ) from error
        elif self.return_url is not None or self.state is not None or self.completion_challenge is not None:
            raise ValueError("Noninteractive authorization does not accept browser parameters")
        if (self.method == "credentials") != (self.credentials is not None):
            raise ValueError("Credentials are supplied only for direct credential authorization")
        return self


class AuthorizationAction(StrictModel):
    type: Literal["open_url", "check_connection", "restart"]
    url: str | None = Field(default=None, max_length=8192, repr=False)


class Authorization(StrictModel):
    id: str
    connection_id: str
    status: Literal[
        "preparing", "awaiting_user", "awaiting_completion", "processing", "completed", "failed", "expired", "cancelled"
    ]
    next_action: AuthorizationAction | None = None
    error_code: str | None = None
    outcome_unknown: bool = False
    expires_at: datetime
    updated_at: datetime


class CompleteAuthorizationRequest(StrictModel):
    receipt: str = Field(min_length=32, max_length=512, repr=False)
    completion_verifier: str = Field(min_length=43, max_length=128, repr=False)


class LaunchAuthorizationRequest(StrictModel):
    token: str = Field(min_length=32, max_length=512, repr=False)
    browser_nonce: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)


class ReceiveAuthorizationRequest(StrictModel):
    browser_nonce: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    session_uri: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)


class AuthorizationRedirect(StrictModel):
    url: str = Field(max_length=8192, repr=False)
