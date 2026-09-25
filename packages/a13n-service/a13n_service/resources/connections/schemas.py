"""Connection API values: an MCP server or a connector app account; credentials are accepted, never returned."""

from datetime import datetime
from typing import Annotated, Literal, Self

from a13n_harness.providers.connector.contracts import ConnectorKey
from a13n_harness.tools import ToolPermissionSetting
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from a13n_service.infra.ids import ObjectId
from a13n_service.providers.tools import MAX_TOOLS, ToolInfo, unique
from a13n_service.providers.tools.mcp import MAX_HEADERS, McpConfig, ToolName
from a13n_service.resources.connections.headers import normalize_headers
from a13n_service.resources.connections.tables import ConnectionAuth, ConnectionStatus, OperationKind

ActionName = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class ConnectorConfig(BaseModel):
    """One app of a connector provider and the actions it exposes; `setup` is validated by the provider type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    app: ConnectorKey
    actions: Annotated[tuple[ActionName, ...], AfterValidator(unique)] = Field(min_length=1, max_length=MAX_TOOLS)
    setup: dict[str, JsonValue] = Field(default_factory=dict)


type ConnectionConfig = McpConfig | ConnectorConfig


def exposed_tools(config: ConnectionConfig) -> tuple[str, ...] | None:
    """The tools a connection offers agents; None is everything an MCP server lists."""
    return config.tools if isinstance(config, McpConfig) else config.actions


class BearerCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator("token")
    @classmethod
    def valid_token(cls, value: SecretStr) -> SecretStr:
        normalize_headers({"authorization": "Bearer " + value.get_secret_value()}, authentication=True)
        return value


class HeadersCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headers: dict[str, SecretStr] = Field(min_length=1, max_length=MAX_HEADERS)

    @field_validator("headers")
    @classmethod
    def valid_headers(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        normalized = normalize_headers({name: secret.get_secret_value() for name, secret in value.items()})
        return {name: SecretStr(secret) for name, secret in normalized.items()}


# Only entered credentials are writable; OAuth tokens and connector accounts come from `authorize`.
type EnteredCredential = BearerCredential | HeadersCredential

# The secret of an OAuth client registered in advance that authenticates with one
# (`config.oauth.token_endpoint_auth_method` other than `none`); it is kept until the server or client changes.
ClientSecret = Annotated[SecretStr, Field(min_length=1, max_length=4096)]


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    config: ConnectionConfig
    auth: ConnectionAuth = "none"
    # Required exactly for connector types: the provider resource serving the app.
    connector_provider_id: ObjectId | None = None
    credential: EnteredCredential | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})
    client_secret: ClientSecret | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    # Replaces the whole configuration; changing the server, app, setup or client drops the credential.
    config: ConnectionConfig | None = None
    auth: ConnectionAuth | None = None
    # Replaced whole when present; `null` removes it.
    credential: EnteredCredential | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})
    # Replaced when present, which also drops the tokens obtained with the old one; `null` removes it.
    client_secret: ClientSecret | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})
    # A disabled connection is listed and refuses new use.
    enabled: bool | None = None


class ConnectionFailure(BaseModel):
    """Why the last remote operation failed; `outcome_unknown` means it may have taken effect."""

    operation_id: str
    operation_kind: OperationKind
    reason: Literal["rejected", "outcome_unknown"]
    code: str | None


class ConnectionTestOutcome(BaseModel):
    """What a test found for the connection version it tested."""

    connection_version: int
    status: Literal["succeeded", "failed"]
    # Safe to show: fixed text or a provider's classified code.
    message: str | None
    tested_at: datetime


class Connection(BaseModel):
    id: str
    organization_id: str
    workspace_id: str
    type: str
    name: str
    config: ConnectionConfig
    auth: ConnectionAuth
    connector_provider_id: str | None
    status: ConnectionStatus
    failure: ConnectionFailure | None
    credential_configured: bool
    client_secret_configured: bool
    # A browser authorization was started and has neither completed nor expired.
    authorization_pending: bool
    # The latest test; recording it keeps the version.
    last_test: ConnectionTestOutcome | None
    enabled: bool
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class RevokedConnection(Connection):
    # Whether the remote side was asked to end the cleared credential and did: `skipped` when there was none to
    # end, the connection type cannot revoke remotely, or the connection or its provider is disabled.
    remote_revocation: Literal["revoked", "failed", "skipped"]


class ConnectionPage(BaseModel):
    items: list[Connection]
    next_cursor: str | None


class ConnectionTest(ConnectionTestOutcome):
    connection_id: str
    # Every tool the server or app lists, including those beyond the connection's selection.
    tools: list[ToolInfo]


class ToolPage(BaseModel):
    items: list[ToolInfo]
    next_cursor: str | None


class AuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # One of the deployment's return URLs; without one the callback answers with the outcome itself.
    return_url: str | None = Field(default=None, max_length=2048)


class AuthorizationResult(BaseModel):
    """A browser flow to follow, or none when the grant obtained the credential directly."""

    # Where to send the browser; None after a client-credentials grant, which needs no browser.
    redirect_url: str | None
    # When the pending browser flow expires; None without one.
    expires_at: datetime | None


class OAuthRedirect(BaseModel):
    # The Service's authorization callback, which an OAuth client registered in advance must allow.
    redirect_uri: str


class CallbackOutcome(BaseModel):
    connection_id: str
    status: ConnectionStatus
    error: str | None


class ConnectionSelection(BaseModel):
    """An agent's use of one connection: all the tools it exposes, or a subset, and their permissions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    connection_id: ObjectId
    tools: Annotated[tuple[ToolName, ...], Field(min_length=1, max_length=MAX_TOOLS), AfterValidator(unique)] | None = (
        None
    )
    # MCP only: the model discovers the tools through tool search instead of seeing every definition upfront.
    defer_loading: bool = False
    # The permission of every tool of the connection without its own entry in `permissions`.
    permission: ToolPermissionSetting = "inherit"
    permissions: dict[ToolName, ToolPermissionSetting] = Field(default_factory=dict, max_length=MAX_TOOLS)

    @model_validator(mode="after")
    def permissions_of_selected(self) -> Self:
        if self.tools is not None and not self.permissions.keys() <= set(self.tools):
            raise ValueError("Tool permissions must name selected tools")
        return self
