"""Non-secret connection authoring and safe readiness projections."""

from typing import Annotated, Literal, Self

from a13n_harness.providers.connector.contracts import ConnectorKey
from pydantic import BaseModel, ConfigDict, Field, model_validator

from a13n_service.infra.http import require_match
from a13n_service.infra.ids import ObjectId
from a13n_service.providers.tools import MAX_TOOLS
from a13n_service.providers.tools.mcp import MAX_HEADERS, HeaderName, McpConfig, OAuthSettings, ToolName
from a13n_service.resources.connections.schemas import (
    ActionName,
    Connection,
    ConnectionAuth,
    ConnectionConfig,
    ConnectionCreate,
    ConnectionFailure,
    ConnectionTestOutcome,
    ConnectionUpdate,
    ConnectorConfig,
)


class ConnectorChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    app: ConnectorKey
    actions: tuple[ActionName, ...] = Field(min_length=1, max_length=MAX_TOOLS)


class ConnectionCreation(BaseModel):
    """Secret entry and connector-specific setup are completed in the trusted configuration UI."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    configuration: McpConfig | ConnectorChoice
    auth: ConnectionAuth = "none"
    connector_provider_id: ObjectId | None = None

    def request(self) -> ConnectionCreate:
        config = self.configuration
        if isinstance(config, ConnectorChoice):
            config = ConnectorConfig(app=config.app, actions=config.actions)
        return ConnectionCreate(
            type=self.type,
            name=self.name,
            config=config,
            auth=self.auth,
            connector_provider_id=self.connector_provider_id,
        )


class ConnectionChanges(BaseModel):
    """Change explicit metadata or non-secret settings; unmentioned configuration survives."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    auth: ConnectionAuth | None = None
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    tools: Annotated[tuple[ToolName, ...], Field(min_length=1, max_length=MAX_TOOLS)] | None = None
    header_names: Annotated[tuple[HeaderName, ...], Field(max_length=MAX_HEADERS)] | None = None
    oauth: OAuthSettings | None = None
    forward_context_headers: bool | None = None
    app: ConnectorKey | None = None
    actions: tuple[ActionName, ...] | None = Field(default=None, min_length=1, max_length=MAX_TOOLS)

    @model_validator(mode="after")
    def clearable(self) -> Self:
        for field in self.model_fields_set - {"tools", "oauth"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


def apply_changes(current: Connection, changes: ConnectionChanges, expected_version: str) -> ConnectionUpdate:
    require_match(expected_version, current.id, current.version)
    supplied = changes.model_fields_set
    result = {name: getattr(changes, name) for name in supplied & {"name", "enabled", "auth"}}
    configuration_fields = {"url", "tools", "header_names", "oauth", "forward_context_headers", "app", "actions"}
    if supplied & configuration_fields:
        config = dict(current.config)
        for name in supplied & configuration_fields:
            config["headers" if name == "header_names" else name] = getattr(changes, name)
        cls = McpConfig if isinstance(current.config, McpConfig) else ConnectorConfig
        result["config"] = cls.model_validate(config)
    return ConnectionUpdate.model_validate(result)


class SafeConnection(BaseModel):
    """No entered credential, OAuth token, client secret or connector setup value is model-visible."""

    id: str
    name: str
    type: str
    configuration: McpConfig | ConnectorChoice
    auth: ConnectionAuth
    connector_provider_id: str | None
    status: str
    enabled: bool
    version: int
    credential_configured: bool
    client_secret_configured: bool
    authorization_pending: bool
    failure: ConnectionFailure | None
    last_test: ConnectionTestOutcome | None
    setup_required: bool
    remote_revocation: Literal["revoked", "failed", "skipped"] | None = None


def safe_connection(value: Connection) -> SafeConnection:
    config: ConnectionConfig = value.config
    configuration = config if isinstance(config, McpConfig) else ConnectorChoice(app=config.app, actions=config.actions)
    return SafeConnection(
        id=value.id,
        name=value.name,
        type=value.type,
        configuration=configuration,
        auth=value.auth,
        connector_provider_id=value.connector_provider_id,
        status=value.status,
        enabled=value.enabled,
        version=value.version,
        credential_configured=value.credential_configured,
        client_secret_configured=value.client_secret_configured,
        authorization_pending=value.authorization_pending,
        failure=value.failure,
        last_test=value.last_test,
        setup_required=value.status != "ready",
        remote_revocation=getattr(value, "remote_revocation", None),
    )


class SafeConnectionPage(BaseModel):
    items: list[SafeConnection]
    next_cursor: str | None


class AccessSetup(BaseModel):
    status: Literal["setup_required"] = "setup_required"
    connection_id: str | None
    version: int | None
    proposed_connection: ConnectionCreation | None = None
    product_area: Literal["connections"] = "connections"
    instructions: str = "Complete connection configuration or authorization in the trusted Console. Do not provide credentials to the Agent."
