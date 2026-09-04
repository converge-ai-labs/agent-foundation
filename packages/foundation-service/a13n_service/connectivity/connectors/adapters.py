"""Typed application boundary implemented by Connector drivers."""

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
    provider_key: str = Field(min_length=1, max_length=128)
    external_user_correlation: str = Field(min_length=1, max_length=128, repr=False)
    callback_url: str | None = Field(default=None, max_length=4096, repr=False)


class SetupStarted(StrictModel):
    external_ref: str = Field(min_length=1, max_length=2048, repr=False)
    redirect_url: str | None = Field(default=None, max_length=4096, repr=False)
    external_handle: str | None = Field(default=None, max_length=4096, repr=False)
    supports_verified_callback: bool


class ConnectionInspection(StrictModel):
    external_ref: str = Field(min_length=1, max_length=2048, repr=False)
    provider_key: str = Field(min_length=1, max_length=128)
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


class ConnectorAdapterError(Exception):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        outcome_unknown: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown
        self.retry_after_seconds = retry_after_seconds


class ConnectorAdapter(Protocol):
    driver_key: str
    config_versions: frozenset[str]

    def validate_config(self, value: object, *, config_version: str) -> JsonObject: ...

    def normalize_endpoint(self, value: str, *, connector_config: JsonObject) -> str: ...

    def validate_credentials(self, value: dict[str, str], *, config_version: str) -> JsonObject: ...

    def validate_setup(
        self,
        value: object,
        *,
        provider_key: str,
        connector_config: JsonObject,
        config_version: str,
    ) -> JsonObject: ...

    async def test_connector(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
    ) -> None: ...

    async def start_setup(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        setup: JsonObject,
        context: SetupContext,
    ) -> SetupStarted: ...

    async def complete_setup(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        session_uri: str,
        context: SetupContext,
        expected_external_ref: str,
    ) -> ConnectionInspection: ...

    async def inspect_connection(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        external_ref: str,
        expected_provider_key: str,
        expected_external_user_correlation: str,
    ) -> ConnectionInspection: ...

    async def revoke_connection(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        external_ref: str,
        operation_id: str,
    ) -> None: ...

    async def list_tools(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        external_ref: str,
        provider_key: str,
        cursor: str | None,
    ) -> ConnectorToolPage: ...

    async def execute_tool(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        external_ref: str,
        tool_key: str,
        provider_version: str,
        arguments: JsonObject,
        request_id: str,
    ) -> ConnectorToolOutcome: ...
