"""OpenConnector native v1 adapter."""

from __future__ import annotations

from typing import Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

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

_CONFIG_VERSION = "openconnector_native_v1"
_OFFICIAL_ENDPOINT = "https://api.openconnector.dev"
_JSON_OBJECT = TypeAdapter(JsonObject)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OpenConnectorConfig(_StrictModel):
    api_profile: Literal["native_v1"] = "native_v1"
    deployment: Literal["cloud", "self_hosted"]
    enabled_provider_slugs: tuple[str, ...] = Field(min_length=1, max_length=256)


class OpenConnectorSetup(_StrictModel):
    auth_config_id: str = Field(min_length=1, max_length=256)


class OpenConnectorAdapter:
    driver_key = "openconnector"
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
        config = OpenConnectorConfig.model_validate(value)
        if len(set(config.enabled_provider_slugs)) != len(config.enabled_provider_slugs):
            raise ValueError("OpenConnector provider slugs must be unique")
        if any(not _valid_slug(item) for item in config.enabled_provider_slugs):
            raise ValueError("OpenConnector provider slug is invalid")
        return model_json(
            config.model_copy(update={"enabled_provider_slugs": tuple(sorted(config.enabled_provider_slugs))})
        )

    def normalize_endpoint(self, value: str, *, connector_config: JsonObject) -> str:
        endpoint = normalized_endpoint(value)
        config = OpenConnectorConfig.model_validate(connector_config)
        if config.deployment == "cloud" and endpoint != _OFFICIAL_ENDPOINT:
            raise ValueError("OpenConnector cloud uses the official endpoint")
        if config.deployment == "self_hosted" and endpoint == _OFFICIAL_ENDPOINT:
            raise ValueError("OpenConnector self-hosted requires a custom endpoint")
        return endpoint

    def validate_credentials(self, value: dict[str, str], *, config_version: str) -> JsonObject:
        _require_version(config_version)
        if set(value) != {"api_key"} or not 1 <= len(value["api_key"]) <= 4096:
            raise ValueError("OpenConnector credentials are invalid")
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
        config = OpenConnectorConfig.model_validate(connector_config)
        if provider_key not in config.enabled_provider_slugs:
            raise ValueError("OpenConnector provider is not enabled")
        return model_json(OpenConnectorSetup.model_validate(value))

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
            path="/api/v1/connectors/connections?limit=1",
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
        configured = OpenConnectorSetup.model_validate(setup)
        value = await self._http.request(
            "POST",
            endpoint=endpoint,
            path=f"/api/v1/connectors/{path_segment(context.provider_key)}/initiate",
            api_key=required_api_key(credentials),
            json_body={
                "authConfigId": configured.auth_config_id,
                "userId": context.external_user_correlation,
            },
            write=True,
            extra_headers={"idempotency-key": context.attempt_id},
        )
        response = required_object(value)
        return SetupStarted(
            external_ref=required_string(response, "connectionId"),
            redirect_url=same_origin_url(response.get("redirectUrl"), endpoint=endpoint),
            supports_verified_callback=False,
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
        del endpoint, connector_config, credentials, session_uri, context, expected_external_ref
        raise ConnectorAdapterError("callback_not_supported")

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
        value = await self._http.request(
            "GET",
            endpoint=endpoint,
            path=f"/api/v1/connectors/connections/{path_segment(external_ref)}",
            api_key=required_api_key(credentials),
        )
        response = required_object(value)
        return _inspection(
            response,
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
            "DELETE",
            endpoint=endpoint,
            path=f"/api/v1/connectors/connections/{path_segment(external_ref)}",
            api_key=required_api_key(credentials),
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
        del connector_config, external_ref
        params = {"toolkit": provider_key, "limit": "100"}
        if cursor is not None:
            params["cursor"] = cursor
        value = required_object(
            await self._http.request(
                "GET",
                endpoint=endpoint,
                path="/api/v1/tools",
                api_key=required_api_key(credentials),
                params=params,
            )
        )
        raw_items = value.get("items")
        if not isinstance(raw_items, list) or len(raw_items) > 100:
            raise ConnectorAdapterError("invalid_provider_response")
        tools: list[ConnectorTool] = []
        for item in raw_items:
            summary = required_object(item)
            key = required_string(summary, "slug", max_length=128)
            detail = required_object(
                await self._http.request(
                    "GET",
                    endpoint=endpoint,
                    path=f"/api/v1/tools/{path_segment(key)}",
                    api_key=required_api_key(credentials),
                )
            )
            if required_string(detail, "slug", max_length=128) != key:
                raise ConnectorAdapterError("invalid_provider_response")
            tools.append(_tool(detail))
        return ConnectorToolPage(
            items=tuple(tools),
            next_cursor=optional_string(value.get("nextCursor")),
            provider_version=required_string(value, "version", max_length=128),
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
        del connector_config, provider_version
        try:
            value = await self._http.request(
                "POST",
                endpoint=endpoint,
                path=f"/api/v1/tools/{path_segment(tool_key)}/execute",
                api_key=required_api_key(credentials),
                json_body={"connectedAccountId": external_ref, "arguments": arguments},
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
        status = response.get("status")
        if type(status) is not int or not 200 <= status < 300:
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
    if required_string(value, "providerSlug", max_length=128) != provider_key:
        raise ConnectorAdapterError("provider_mismatch")
    if required_string(value, "userId", max_length=128) != external_user_correlation:
        raise ConnectorAdapterError("owner_mismatch")
    status, reason = _status(required_string(value, "status", max_length=64), value.get("disabled"))
    return ConnectionInspection(
        external_ref=external_ref,
        provider_key=provider_key,
        external_user_correlation=external_user_correlation,
        status=status,
        status_reason=reason,
        safe_metadata={
            "auth_method_kind": optional_string(value.get("authMethodKind"), max_length=128),
            "alias": optional_string(value.get("alias"), max_length=256),
            "created_at": optional_string(value.get("createdAt"), max_length=64),
            "updated_at": optional_string(value.get("updatedAt"), max_length=64),
        },
        provider_version="native_v1",
    )


def _status(value: str, disabled: object) -> tuple[AdapterConnectionStatus, AdapterStatusReason | None]:
    if disabled is True:
        return AdapterConnectionStatus.disabled, None
    if value == "pending":
        return AdapterConnectionStatus.pending, None
    if value == "active":
        return AdapterConnectionStatus.ready, None
    if value in {"expired", "revoked", "dropped"}:
        return AdapterConnectionStatus.action_required, AdapterStatusReason.reauthorization_required
    return AdapterConnectionStatus.action_required, AdapterStatusReason.incompatible


def _tool(value: JsonObject) -> ConnectorTool:
    input_schema = required_object(value.get("inputParameters"))
    output = value.get("outputParameters")
    return ConnectorTool(
        key=required_string(value, "slug", max_length=128),
        description=optional_string(value.get("description"), max_length=16_384) or "",
        input_schema=input_schema,
        output_schema=required_object(output) if output is not None else None,
        annotations={},
    )


def _valid_slug(value: str) -> bool:
    return 1 <= len(value) <= 128 and all(character.isalnum() or character in "_-" for character in value)


def _require_version(value: str) -> None:
    if value != _CONFIG_VERSION:
        raise ValueError("unsupported OpenConnector configuration version")
