"""Public Remote MCP management contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

from a13n_service.connectivity.domain import JsonObject

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


OAuthTokenAuthMethod = Literal["none", "client_secret_basic", "client_secret_post"]
OAuthGrantType = Literal["authorization_code", "client_credentials"]
OAuthClientSource = Literal["pre_registered", "dynamic", "metadata_document"]


class MCPOAuthClientConfiguration(StrictModel):
    issuer_url: Endpoint
    client_id: str = Field(min_length=1, max_length=2048)
    token_endpoint_auth_method: OAuthTokenAuthMethod
    grant_type: OAuthGrantType
    source: OAuthClientSource
    redirect_uri: Endpoint | None = None


class MCPOAuthClientInput(StrictModel):
    issuer_url: Endpoint
    client_id: str = Field(min_length=1, max_length=2048)
    token_endpoint_auth_method: OAuthTokenAuthMethod
    grant_type: OAuthGrantType = "authorization_code"
    redirect_uri: Endpoint | None = None
    client_secret: SecretStr | None = Field(default=None, min_length=1, max_length=16_384, repr=False)

    @model_validator(mode="after")
    def valid_client(self) -> MCPOAuthClientInput:
        confidential = self.token_endpoint_auth_method != "none" or self.grant_type == "client_credentials"
        if confidential != (self.client_secret is not None):
            raise ValueError("Confidential clients require a secret; public clients must not supply one")
        if self.grant_type == "client_credentials" and self.token_endpoint_auth_method == "none":
            raise ValueError("Client credentials requires authenticated token requests")
        if (self.grant_type == "authorization_code") != (self.redirect_uri is not None):
            raise ValueError("Authorization-code clients require a redirect URI; machine clients must omit it")
        return self


class ConfigureMCPOAuthClientRequest(StrictModel):
    expected_version: int = Field(ge=1)
    client: MCPOAuthClientInput | None


class MCPOAuthSetupRequest(StrictModel):
    redirect_uri: Endpoint | None = None


class MCPOAuthDiscovery(StrictModel):
    issuer_url: Endpoint
    redirect_uri: Endpoint | None
    token_endpoint_auth_methods_supported: tuple[OAuthTokenAuthMethod, ...]
    grant_types_supported: tuple[OAuthGrantType, ...]
    client_registration: Literal["metadata_document", "dynamic", "manual"]
    authorization_response_iss_parameter_supported: bool


class MCPOAuthSetupAction(StrictModel):
    type: Literal[
        "configure_oauth_client",
        "start_authorization",
        "authenticate_client_credentials",
        "check_connection",
        "completed",
    ]
    redirect_uri: Endpoint | None = None
    issuer_url: Endpoint | None = None
    token_endpoint_auth_methods: tuple[OAuthTokenAuthMethod, ...] = ()
    grant_types: tuple[OAuthGrantType, ...] = ()
    client_registration: Literal["metadata_document", "dynamic", "manual"] | None = None
    documentation_url: Endpoint | None = None


class MCPOAuthSetup(StrictModel):
    next_action: MCPOAuthSetupAction
    client: MCPOAuthClientConfiguration | None = None


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
    id: str = Field(pattern=r"^authz_[0-9A-Za-z]+$")
    status: str = "pending"
    authorization_url: str | None = Field(max_length=8192, repr=False)
    expires_at: datetime


class MCPTool(StrictModel):
    name: str
    description: str = ""
    input_schema: JsonObject
    output_schema: JsonObject | None = None
    annotations: JsonObject = Field(default_factory=dict)


class MCPToolCollection(StrictModel):
    items: tuple[MCPTool, ...]


class MCPClientMetadata(StrictModel):
    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]
    grant_types: tuple[str, ...] = ("authorization_code",)
    response_types: tuple[str, ...] = ("code",)
    token_endpoint_auth_method: str = "none"
