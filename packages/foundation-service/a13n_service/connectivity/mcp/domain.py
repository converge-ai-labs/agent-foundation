"""Public Remote MCP management contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

from a13n_service.connectivity.ingress.domain import BoundedName, JsonObject
from a13n_service.iam.domain import PrincipalRef

MCP_PROTOCOL_REVISION = "2025-11-25"
Endpoint = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
HeaderName = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MCPAuthMode(StrEnum):
    none = "none"
    bearer = "bearer"
    oauth = "oauth"
    static_headers = "static_headers"


class MCPConnectionStatus(StrEnum):
    pending = "pending"
    ready = "ready"
    action_required = "action_required"
    disabled = "disabled"


class MCPConnectionStatusReason(StrEnum):
    reauthorization_required = "reauthorization_required"
    incompatible = "incompatible"


class MCPConnection(StrictModel):
    id: str
    organization_id: str
    workspace_id: str
    owner_user_id: str | None
    name: BoundedName
    endpoint_url: Endpoint
    auth_mode: MCPAuthMode
    static_header_names: tuple[HeaderName, ...] = Field(max_length=16)
    status: MCPConnectionStatus
    status_reason: MCPConnectionStatusReason | None
    version: int = Field(ge=1)
    credential_configured: bool
    credential_generation: int = Field(ge=0)
    catalog_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def valid_status(self) -> MCPConnection:
        if (self.status is MCPConnectionStatus.action_required) != (self.status_reason is not None):
            raise ValueError("status_reason is required exactly for action_required")
        if self.auth_mode is MCPAuthMode.static_headers:
            if not self.static_header_names:
                raise ValueError("static_headers requires header names")
        elif self.static_header_names:
            raise ValueError("static header names require static_headers auth mode")
        return self


class MCPConnectionCollection(StrictModel):
    items: tuple[MCPConnection, ...]
    next_cursor: str | None = None


class CreateMCPConnectionRequest(StrictModel):
    name: BoundedName
    endpoint_url: Endpoint
    auth_mode: MCPAuthMode
    owner_user_id: str | None = Field(default=None, min_length=1, max_length=72)
    static_header_names: tuple[HeaderName, ...] = Field(default=(), max_length=16)


class UpdateMCPConnectionRequest(StrictModel):
    expected_version: int = Field(ge=1)
    name: BoundedName


class MCPConnectionCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)


class ReplaceMCPCredentialsRequest(MCPConnectionCommandRequest):
    bearer: SecretStr | None = Field(default=None, min_length=1, max_length=16_384, repr=False)
    static_headers: dict[HeaderName, SecretStr] | None = Field(
        default=None,
        min_length=1,
        max_length=16,
        repr=False,
        json_schema_extra={"writeOnly": True},
    )


class MCPAuthorizationLaunch(StrictModel):
    id: str = Field(pattern=r"^mos_[0-9A-Za-z]+$")
    status: Literal["pending"] = "pending"
    authorization_url: str = Field(max_length=8192, repr=False)
    expires_at: datetime


class MCPTool(StrictModel):
    name: str
    description: str = ""
    input_schema: JsonObject
    output_schema: JsonObject | None = None
    annotations: JsonObject = Field(default_factory=dict)


class MCPClientMetadata(StrictModel):
    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]
    grant_types: tuple[str, ...] = ("authorization_code",)
    response_types: tuple[str, ...] = ("code",)
    token_endpoint_auth_method: str = "none"
