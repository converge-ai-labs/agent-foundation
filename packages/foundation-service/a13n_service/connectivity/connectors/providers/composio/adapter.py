"""Composio v3.1 Connector adapter."""

from __future__ import annotations

import re
from typing import Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.http import EndpointValidator

from ...adapters import (
    AdapterConnectionStatus,
    AdapterStatusReason,
    ConnectionInspection,
    ConnectorAdapterError,
    ConnectorTool,
    ConnectorToolOutcome,
    ConnectorToolPage,
    SetupContext,
    SetupStarted,
)
from ...http import ConnectorHttpClient, required_api_key
from ..common.validation import (
    model_json,
    normalized_endpoint,
    optional_string,
    path_segment,
    required_object,
    required_string,
    same_origin_url,
)

_CONFIG_VERSION = "composio_v3_1"
_OFFICIAL_ENDPOINT = "https://backend.composio.dev"
_TOOLKIT_VERSION = re.compile(r"^[0-9]{8}_[0-9]{2}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ComposioConfig(_StrictModel):
    connected_accounts_profile: Literal["v3_1"] = "v3_1"
    tools_profile: Literal["v3_1"] = "v3_1"
    enabled_toolkits: tuple[str, ...] = Field(min_length=1, max_length=256)
    project_identity: str | None = Field(default=None, min_length=1, max_length=256)


class ComposioSetup(_StrictModel):
    auth_config_id: str = Field(min_length=1, max_length=256)
    toolkit_version: str = Field(pattern=r"^[0-9]{8}_[0-9]{2}$")


class ComposioAdapter:
    driver_key = "composio"
    config_versions = frozenset({_CONFIG_VERSION})

    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        endpoint_validator: EndpointValidator,
        *,
        response_max_bytes: int,
    ) -> None:
        self._http = ConnectorHttpClient(
            http_client,
            endpoint_validator,
            response_max_bytes=response_max_bytes,
        )

    def validate_config(self, value: object, *, config_version: str) -> JsonObject:
        _require_version(config_version)
        config = ComposioConfig.model_validate(value)
        if len(set(config.enabled_toolkits)) != len(config.enabled_toolkits):
            raise ValueError("Composio toolkits must be unique")
        if any(not _valid_slug(item) for item in config.enabled_toolkits):
            raise ValueError("Composio toolkit is invalid")
        return model_json(config.model_copy(update={"enabled_toolkits": tuple(sorted(config.enabled_toolkits))}))

    def normalize_endpoint(self, value: str, *, connector_config: JsonObject) -> str:
        ComposioConfig.model_validate(connector_config)
        return normalized_endpoint(value)

    def validate_credentials(self, value: dict[str, str], *, config_version: str) -> JsonObject:
        _require_version(config_version)
        if set(value) != {"api_key"} or not 1 <= len(value["api_key"]) <= 4096:
            raise ValueError("Composio credentials are invalid")
        return dict(value)

    def validate_setup(
        self,
        value: object,
        *,
        provider_key: str,
        connector_config: JsonObject,
        config_version: str,
    ) -> JsonObject:
        _require_version(config_version)
        config = ComposioConfig.model_validate(connector_config)
        if provider_key not in config.enabled_toolkits:
            raise ValueError("Composio toolkit is not enabled")
        return model_json(ComposioSetup.model_validate(value))

    async def test_connector(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
    ) -> None:
        del connector_config
        await self._http.request(
            "GET",
            endpoint=endpoint,
            path="/api/v3.1/connected_accounts?limit=1",
            api_key=required_api_key(credentials),
        )

    async def start_setup(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        setup: JsonObject,
        context: SetupContext,
    ) -> SetupStarted:
        del connector_config
        configured = ComposioSetup.model_validate(setup)
        if context.callback_url is None:
            raise ConnectorAdapterError("callback_unavailable")
        value = required_object(
            await self._http.request(
                "POST",
                endpoint=endpoint,
                path="/api/v3.1/connected_accounts/link",
                api_key=required_api_key(credentials),
                json_body={
                    "auth_config_id": configured.auth_config_id,
                    "callback_url": context.callback_url,
                    "user_id": context.external_user_correlation,
                },
                write=True,
                extra_headers={"idempotency-key": context.attempt_id},
            )
        )
        return SetupStarted(
            external_ref=required_string(value, "connected_account_id"),
            external_handle=required_string(value, "session_uri", max_length=4096),
            redirect_url=same_origin_url(value.get("redirect_url"), endpoint=endpoint),
            supports_verified_callback=True,
        )

    async def complete_setup(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        session_uri: str,
        context: SetupContext,
        expected_external_ref: str,
    ) -> ConnectionInspection:
        del connector_config
        value = required_object(
            await self._http.request(
                "POST",
                endpoint=endpoint,
                path="/api/v3.1/connected_accounts/complete_auth",
                api_key=required_api_key(credentials),
                json_body={
                    "session_uri": session_uri,
                    "user_id": context.external_user_correlation,
                },
                write=True,
                extra_headers={"idempotency-key": context.attempt_id},
            )
        )
        return _inspection(
            value,
            external_ref=expected_external_ref,
            provider_key=context.provider_key,
            external_user_correlation=context.external_user_correlation,
        )

    async def inspect_connection(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        external_ref: str,
        expected_provider_key: str,
        expected_external_user_correlation: str,
    ) -> ConnectionInspection:
        del connector_config
        value = required_object(
            await self._http.request(
                "GET",
                endpoint=endpoint,
                path=f"/api/v3.1/connected_accounts/{path_segment(external_ref)}",
                api_key=required_api_key(credentials),
            )
        )
        return _inspection(
            value,
            external_ref=external_ref,
            provider_key=expected_provider_key,
            external_user_correlation=expected_external_user_correlation,
        )

    async def revoke_connection(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        external_ref: str,
        operation_id: str,
    ) -> None:
        del connector_config
        await self._http.request(
            "POST",
            endpoint=endpoint,
            path=f"/api/v3.1/connected_accounts/{path_segment(external_ref)}/revoke",
            api_key=required_api_key(credentials),
            json_body={},
            write=True,
            extra_headers={"idempotency-key": operation_id},
        )

    async def list_tools(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        external_ref: str,
        provider_key: str,
        cursor: str | None,
    ) -> ConnectorToolPage:
        del external_ref
        config = ComposioConfig.model_validate(connector_config)
        params = {"toolkit_slug": provider_key, "limit": "100"}
        if cursor is not None:
            params["cursor"] = cursor
        value = required_object(
            await self._http.request(
                "GET",
                endpoint=endpoint,
                path="/api/v3.1/tools",
                api_key=required_api_key(credentials),
                params=params,
            )
        )
        raw_items = value.get("items")
        if not isinstance(raw_items, list) or len(raw_items) > 100:
            raise ConnectorAdapterError("invalid_provider_response")
        tools = tuple(_tool(required_object(item)) for item in raw_items)
        version = required_string(value, "toolkit_version", max_length=128)
        if _TOOLKIT_VERSION.fullmatch(version) is None:
            raise ConnectorAdapterError("incompatible_toolkit_version")
        if provider_key not in config.enabled_toolkits:
            raise ConnectorAdapterError("provider_mismatch")
        return ConnectorToolPage(
            items=tools,
            next_cursor=optional_string(value.get("next_cursor")),
            provider_version=version,
        )

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
    ) -> ConnectorToolOutcome:
        ComposioConfig.model_validate(connector_config)
        if _TOOLKIT_VERSION.fullmatch(provider_version) is None:
            raise ConnectorAdapterError("incompatible_toolkit_version")
        try:
            value = await self._http.request(
                "POST",
                endpoint=endpoint,
                path=f"/api/v3.1/tools/execute/{path_segment(tool_key)}",
                api_key=required_api_key(credentials),
                json_body={
                    "arguments": arguments,
                    "connected_account_id": external_ref,
                    "toolkit_version": provider_version,
                },
                write=True,
                extra_headers={"idempotency-key": request_id},
            )
        except ConnectorAdapterError as error:
            if error.outcome_unknown:
                return ConnectorToolOutcome(kind="outcome_unknown", request_id=request_id)
            raise
        response = required_object(value)
        if response.get("successful") is not True:
            raise ConnectorAdapterError("tool_rejected")
        return ConnectorToolOutcome(kind="succeeded", result=response.get("data"), request_id=request_id)


def _inspection(
    value: JsonObject,
    *,
    external_ref: str,
    provider_key: str,
    external_user_correlation: str,
) -> ConnectionInspection:
    if required_string(value, "id") != external_ref:
        raise ConnectorAdapterError("connection_substitution")
    if required_string(value, "toolkit_slug", max_length=128) != provider_key:
        raise ConnectorAdapterError("provider_mismatch")
    if required_string(value, "user_id", max_length=128) != external_user_correlation:
        raise ConnectorAdapterError("owner_mismatch")
    status, reason = _status(required_string(value, "status", max_length=64))
    return ConnectionInspection(
        external_ref=external_ref,
        provider_key=provider_key,
        external_user_correlation=external_user_correlation,
        status=status,
        status_reason=reason,
        safe_metadata={
            "display_name": optional_string(value.get("display_name"), max_length=256),
            "created_at": optional_string(value.get("created_at"), max_length=64),
            "updated_at": optional_string(value.get("updated_at"), max_length=64),
        },
        provider_version="v3_1",
    )


def _status(value: str) -> tuple[AdapterConnectionStatus, AdapterStatusReason | None]:
    if value in {"INITIALIZING", "PENDING"}:
        return AdapterConnectionStatus.pending, None
    if value == "ACTIVE":
        return AdapterConnectionStatus.ready, None
    if value == "DISABLED":
        return AdapterConnectionStatus.disabled, None
    if value in {"EXPIRED", "REVOKED"}:
        return AdapterConnectionStatus.action_required, AdapterStatusReason.reauthorization_required
    return AdapterConnectionStatus.action_required, AdapterStatusReason.incompatible


def _tool(value: JsonObject) -> ConnectorTool:
    return ConnectorTool(
        key=required_string(value, "slug", max_length=128),
        description=optional_string(value.get("description"), max_length=16_384) or "",
        input_schema=required_object(value.get("input_parameters")),
        output_schema=(
            required_object(value.get("output_parameters")) if value.get("output_parameters") is not None else None
        ),
        annotations={},
    )


def _valid_slug(value: str) -> bool:
    return 1 <= len(value) <= 128 and all(character.isalnum() or character in "_-" for character in value)


def _require_version(value: str) -> None:
    if value != _CONFIG_VERSION:
        raise ValueError("unsupported Composio configuration version")
