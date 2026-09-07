"""Typed application boundary implemented by ConnectorProvider drivers."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from a13n_service.connectivity.domain import JsonObject


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
    attempt_id: str
    generation: int = Field(ge=1)
    connector_key: str = Field(min_length=1, max_length=128)
    external_user_correlation: str = Field(min_length=1, max_length=128, repr=False)
    callback_url: str | None = Field(default=None, max_length=4096, repr=False)


class SetupStarted(StrictModel):
    setup_ref: str = Field(min_length=1, max_length=2048, repr=False)
    external_ref: str | None = Field(default=None, min_length=1, max_length=2048, repr=False)
    redirect_url: str | None = Field(default=None, max_length=4096, repr=False)
    external_handle: str | None = Field(default=None, max_length=4096, repr=False)
    supports_verified_callback: bool


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


class ConnectorToolOutcome(StrictModel):
    kind: Literal["succeeded", "outcome_unknown"]
    result: JsonValue | None = None
    request_id: str | None = Field(default=None, max_length=128)


class ConnectorProviderError(Exception):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        outcome_unknown: bool = False,
        retry_after_seconds: int | None = None,
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


class ConnectorConnectionRuntime(Protocol):
    """One verified external account; construction and close have no remote effects."""

    async def inspect(self) -> ConnectionInspection: ...

    async def discover_tools(self, *, cursor: str | None) -> ConnectorToolPage: ...

    async def execute_tool(
        self, *, tool_key: str, provider_version: str, arguments: JsonObject, request_id: str
    ) -> ConnectorToolOutcome: ...

    async def revoke(self, *, operation_id: str) -> None: ...

    async def aclose(self) -> None: ...


class ToolCatalog(Protocol):
    async def discover_tools(self, *, cursor: str | None) -> ConnectorToolPage: ...


ProviderAccess = Literal["catalog_read", "account_read"]


class ConnectorProviderRuntime(Protocol):
    compatibility_profile: str
    setup_replay_safe: bool

    async def test(self) -> tuple[ProviderAccess, ...]: ...

    async def discover_connectors(self) -> tuple[DiscoveredConnector, ...]: ...

    async def start_setup(
        self, *, setup: JsonObject, context: SetupContext, resume_ref: str | None = None
    ) -> SetupStarted: ...

    async def complete_setup(
        self, *, session_uri: str, context: SetupContext, expected_external_ref: str
    ) -> ConnectionInspection: ...

    async def inspect_setup(self, *, setup_ref: str, context: SetupContext) -> ConnectionInspection | None: ...

    def tool_catalog(self, connector_key: str) -> ToolCatalog: ...

    def connect(self, binding: ConnectionBinding) -> ConnectorConnectionRuntime: ...

    async def aclose(self) -> None: ...


class DiscoveredConnector(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,127}$")
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=16_384)
    setup_schema: JsonObject
    authentication_methods: tuple[str, ...] = Field(max_length=32)
