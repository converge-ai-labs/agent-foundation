"""Typed application boundary implemented by ConnectorProvider drivers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

type JsonObject = dict[str, JsonValue]

ConnectorKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_][a-z0-9._-]{0,127}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AdapterConnectionStatus(StrEnum):
    pending = "pending"
    ready = "ready"
    action_required = "action_required"
    disabled = "disabled"


class AdapterStatusReason(StrEnum):
    reauthorization_required = "reauthorization_required"
    incompatible = "incompatible"


class SetupContext(StrictModel):
    connector_key: str = Field(min_length=1, max_length=128)
    external_user_correlation: str = Field(min_length=1, max_length=128, repr=False)
    callback_url: str | None = Field(default=None, max_length=4096, repr=False)


class SetupCompletionMethod(StrEnum):
    polling = "polling"
    oauth_verifier = "oauth_verifier"
    browser_confirmation = "browser_confirmation"


class SetupStarted(StrictModel):
    setup_ref: str = Field(min_length=1, max_length=2048, repr=False)
    external_ref: str | None = Field(default=None, min_length=1, max_length=2048, repr=False)
    redirect_url: str | None = Field(default=None, max_length=4096, repr=False)
    expires_at: datetime | None = None
    completion_method: SetupCompletionMethod


class ConnectionInspection(StrictModel):
    external_ref: str = Field(min_length=1, max_length=2048, repr=False)
    connector_key: str = Field(min_length=1, max_length=128)
    external_user_correlation: str = Field(min_length=1, max_length=128, repr=False)
    status: AdapterConnectionStatus
    status_reason: AdapterStatusReason | None = None
    safe_metadata: JsonObject
    provider_version: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def valid_reason(self) -> ConnectionInspection:
        if (self.status is AdapterConnectionStatus.action_required) != (self.status_reason is not None):
            raise ValueError("status_reason is required exactly for action_required")
        return self


class ConnectorTool(StrictModel):
    provider_version: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=128)
    description: str = Field(max_length=16_384)
    input_schema: JsonObject
    output_schema: JsonObject | None = None
    annotations: JsonObject = Field(default_factory=dict)


class ConnectorToolPage(StrictModel):
    items: tuple[ConnectorTool, ...]
    next_cursor: str | None = Field(default=None, max_length=2048)
    provider_version: str = Field(min_length=1, max_length=128)


class ConnectorToolFailure(StrictModel):
    code: Literal[
        "scope_missing", "permission_denied", "authentication_required", "not_found", "rate_limited", "tool_rejected"
    ]
    message: str = Field(max_length=256)


class ConnectorToolOutcome(StrictModel):
    kind: Literal["succeeded", "failed", "outcome_unknown"]
    result: JsonValue | None = None
    request_id: str | None = Field(default=None, max_length=128)
    error: ConnectorToolFailure | None = None

    @model_validator(mode="after")
    def validate_failure(self) -> ConnectorToolOutcome:
        if (self.kind == "failed") != (self.error is not None):
            raise ValueError("error is required exactly for failed tool outcomes")
        return self


class ConnectorProviderError(Exception):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        outcome_unknown: bool = False,
        retry_after_seconds: float | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown
        self.retry_after_seconds = retry_after_seconds
        self.http_status = http_status


class ConnectionBinding(StrictModel):
    external_user_correlation: str = Field(min_length=1, max_length=128, repr=False)
    external_ref: str = Field(min_length=1, max_length=2048, repr=False)
    connector_key: str = Field(min_length=1, max_length=128)


BeforeDispatch = Callable[[], Awaitable[None]]
BeforeSharedSetup = Callable[[str], Awaitable[None]]


class ConnectorConnectionRuntime(Protocol):
    """One verified external account; creating a handle has no remote effects."""

    async def inspect(self) -> ConnectionInspection: ...

    async def discover_tools(self, *, cursor: str | None) -> ConnectorToolPage: ...

    async def execute_tool(
        self,
        *,
        tool_key: str,
        provider_version: str,
        arguments: JsonObject,
        request_id: str,
        before_dispatch: BeforeDispatch | None = None,
    ) -> ConnectorToolOutcome: ...

    async def revoke(self, *, operation_id: str) -> None: ...


class ToolCatalog(Protocol):
    async def discover_tools(self, *, cursor: str | None) -> ConnectorToolPage: ...


ProviderAccess = Literal["catalog_read", "account_read"]


class ConnectorProviderRuntime(Protocol):
    compatibility_profile: str
    setup_replay_safe: bool

    async def test(self) -> tuple[ProviderAccess, ...]: ...

    async def discover_connectors(self) -> tuple[DiscoveredConnector, ...]: ...

    async def discover_connector(self, connector_key: str) -> DiscoveredConnector: ...

    async def start_setup(
        self,
        *,
        setup: JsonObject,
        context: SetupContext,
        resume_ref: str | None = None,
        before_shared_setup: BeforeSharedSetup | None = None,
        credentials: JsonObject | None = None,
    ) -> SetupStarted: ...

    async def complete_setup(
        self, *, session_uri: str, context: SetupContext, expected_external_ref: str
    ) -> ConnectionInspection: ...

    async def inspect_setup(self, *, setup_ref: str, context: SetupContext) -> ConnectionInspection | None: ...

    def tool_catalog(self, connector_key: str) -> ToolCatalog: ...

    def connect(self, binding: ConnectionBinding) -> ConnectorConnectionRuntime: ...


class DiscoveredConnector(StrictModel):
    key: ConnectorKey
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=16_384)
    logo_url: str | None = Field(default=None, max_length=2048)
    unavailable_reason: str | None = Field(default=None, max_length=512)
    setup_schema: JsonObject
    authentication_methods: tuple[str, ...] = Field(max_length=32)
    credential_schemas: dict[str, JsonObject] = Field(default_factory=dict)
