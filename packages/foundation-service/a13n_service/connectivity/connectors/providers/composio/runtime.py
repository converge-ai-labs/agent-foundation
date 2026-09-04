"""Composio v3.1 ConnectorProvider adapter."""

from __future__ import annotations

import re

from a13n_service.connectivity.connectors.providers.configuration import ApiKeyCredentials
from a13n_service.connectivity.domain import JsonObject

from ...contracts import (
    AdapterConnectionStatus,
    AdapterStatusReason,
    ConnectionBinding,
    ConnectionInspection,
    ConnectorProviderError,
    ConnectorTool,
    ConnectorToolOutcome,
    ConnectorToolPage,
    DiscoveredConnector,
    SetupContext,
    SetupStarted,
)
from ...http import ConnectorHttpClient
from ...validation import (
    optional_string,
    path_segment,
    required_object,
    required_string,
    same_origin_url,
)
from ..discovery import DirectoryBudget, directory_items, setup_schema, validate_discovered_setup
from .configuration import ComposioConfiguration, ComposioSetup

_TOOLKIT_VERSION = re.compile(r"^[0-9]{8}_[0-9]{2}$")


class ComposioProvider:
    compatibility_profile = "composio_v3_1"

    def __init__(
        self, http: ConnectorHttpClient, configuration: ComposioConfiguration, credentials: ApiKeyCredentials
    ) -> None:
        self._http = http
        self._configuration = configuration
        self._credentials = credentials

    async def aclose(self) -> None:
        # The process owns the shared HTTP client.
        pass

    def connect(self, binding: ConnectionBinding) -> ComposioConnection:
        if binding.connector_key not in self._configuration.enabled_toolkits:
            raise ConnectorProviderError("connector_not_enabled")
        return ComposioConnection(self._http, self._configuration, self._credentials, binding)

    async def test(
        self,
    ) -> None:
        await self._http.request(
            "GET",
            endpoint=self._configuration.endpoint,
            path="/api/v3.1/connected_accounts?limit=1",
            api_key=self._credentials.api_key,
        )

    async def discover_connectors(self) -> tuple[DiscoveredConnector, ...]:
        budget = DirectoryBudget()
        toolkits = await directory_items(
            self._http,
            endpoint=self._configuration.endpoint,
            api_key=self._credentials.api_key,
            path="/api/v3.1/toolkits",
            budget=budget,
        )
        auth_configs = await directory_items(
            self._http,
            endpoint=self._configuration.endpoint,
            api_key=self._credentials.api_key,
            path="/api/v3.1/auth_configs",
            budget=budget,
            page_size=50,
        )
        configured: dict[str, list[str]] = {}
        for item in auth_configs:
            # v3.1 hosted OAuth is supported. Other schemes must never solicit credentials in Foundation.
            if item.get("status") != "ENABLED" or item.get("auth_scheme") != "OAUTH2":
                continue
            key = required_string(required_object(item.get("toolkit")), "slug", max_length=128)
            configured.setdefault(key, []).append(required_string(item, "id", max_length=256))
        result: list[DiscoveredConnector] = []
        seen: set[str] = set()
        for item in toolkits:
            key = required_string(item, "slug", max_length=128)
            if key in seen:
                raise ConnectorProviderError("invalid_provider_response")
            seen.add(key)
            if key not in self._configuration.enabled_toolkits:
                continue
            meta = required_object(item.get("meta"))
            version = required_string(meta, "version", max_length=128)
            if _TOOLKIT_VERSION.fullmatch(version) is None:
                raise ConnectorProviderError("incompatible_toolkit_version")
            ids = configured.get(key, [])
            result.append(
                DiscoveredConnector(
                    key=key,
                    name=required_string(item, "name", max_length=128),
                    description=optional_string(meta.get("description"), max_length=16_384),
                    setup_schema=setup_schema(ids, toolkit_version=version) if ids else {"not": {}},
                    authentication_methods=("OAUTH2",) if ids else (),
                )
            )
        return tuple(result)

    async def start_setup(
        self,
        *,
        setup: JsonObject,
        context: SetupContext,
    ) -> SetupStarted:
        configured = ComposioSetup.model_validate(setup)
        await validate_discovered_setup(self.discover_connectors, context.connector_key, setup)
        if context.callback_url is None:
            raise ConnectorProviderError("callback_unavailable")
        value = await self._http.request(
            "POST",
            endpoint=self._configuration.endpoint,
            path="/api/v3.1/connected_accounts/link",
            api_key=self._credentials.api_key,
            json_body={
                "auth_config_id": configured.auth_config_id,
                "callback_url": context.callback_url,
                "user_id": context.external_user_correlation,
            },
            write=True,
            extra_headers={"idempotency-key": context.attempt_id},
        )
        try:
            response = required_object(value)
            return SetupStarted(
                external_ref=required_string(response, "connected_account_id"),
                external_handle=required_string(response, "session_uri", max_length=4096),
                redirect_url=same_origin_url(response.get("redirect_url"), endpoint=self._configuration.endpoint),
                supports_verified_callback=True,
            )
        except ValueError as error:
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=True) from error

    async def complete_setup(
        self,
        *,
        session_uri: str,
        context: SetupContext,
        expected_external_ref: str,
    ) -> ConnectionInspection:
        value = await self._http.request(
            "POST",
            endpoint=self._configuration.endpoint,
            path="/api/v3.1/connected_accounts/complete_auth",
            api_key=self._credentials.api_key,
            json_body={
                "session_uri": session_uri,
                "user_id": context.external_user_correlation,
            },
            write=True,
            extra_headers={"idempotency-key": context.attempt_id},
        )
        try:
            return _inspection(
                required_object(value),
                external_ref=expected_external_ref,
                connector_key=context.connector_key,
                external_user_correlation=context.external_user_correlation,
            )
        except ValueError as error:
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=True) from error


class ComposioToolCatalog:
    """Read a toolkit's definitions without an external account binding."""

    def __init__(
        self,
        http: ConnectorHttpClient,
        configuration: ComposioConfiguration,
        credentials: ApiKeyCredentials,
        connector_key: str,
    ) -> None:
        if connector_key not in configuration.enabled_toolkits:
            raise ConnectorProviderError("connector_not_enabled")
        self._http = http
        self._configuration = configuration
        self._credentials = credentials
        self._connector_key = connector_key
        self._catalog_version: str | None = None

    async def discover_tools(self, *, cursor: str | None) -> ConnectorToolPage:
        if self._catalog_version is None:
            toolkit = required_object(
                await self._http.request(
                    "GET",
                    endpoint=self._configuration.endpoint,
                    path=f"/api/v3.1/toolkits/{path_segment(self._connector_key)}",
                    api_key=self._credentials.api_key,
                )
            )
            if required_string(toolkit, "slug", max_length=128) != self._connector_key:
                raise ConnectorProviderError("provider_mismatch")
            version = required_string(required_object(toolkit.get("meta")), "version", max_length=128)
            if _TOOLKIT_VERSION.fullmatch(version) is None:
                raise ConnectorProviderError("incompatible_toolkit_version")
            self._catalog_version = version
        version = self._catalog_version
        params = {
            "toolkit_slug": self._connector_key,
            f"toolkit_versions[{self._connector_key}]": version,
            "limit": "100",
        }
        if cursor is not None:
            params["cursor"] = cursor
        value = required_object(
            await self._http.request(
                "GET",
                endpoint=self._configuration.endpoint,
                path="/api/v3.1/tools",
                api_key=self._credentials.api_key,
                params=params,
            )
        )
        items = value.get("items")
        if not isinstance(items, list) or len(items) > 100:
            raise ConnectorProviderError("invalid_provider_response")
        tools: list[ConnectorTool] = []
        for item in items:
            summary = required_object(item)
            key = required_string(summary, "slug", max_length=128)
            detail = required_object(
                await self._http.request(
                    "GET",
                    endpoint=self._configuration.endpoint,
                    path=f"/api/v3.1/tools/{path_segment(key)}",
                    api_key=self._credentials.api_key,
                    params={"version": version},
                )
            )
            if (
                required_string(detail, "slug", max_length=128) != key
                or required_string(required_object(detail.get("toolkit")), "slug", max_length=128)
                != self._connector_key
                or required_string(detail, "version", max_length=128) != version
            ):
                raise ConnectorProviderError("incompatible_tool_version")
            tools.append(_tool(detail))
        return ConnectorToolPage(
            items=tuple(tools), next_cursor=optional_string(value.get("next_cursor")), provider_version=version
        )


class ComposioConnection:
    def __init__(
        self,
        http: ConnectorHttpClient,
        configuration: ComposioConfiguration,
        credentials: ApiKeyCredentials,
        binding: ConnectionBinding,
    ) -> None:
        self._http = http
        self._configuration = configuration
        self._credentials = credentials
        self._binding = binding
        self._catalog = ComposioToolCatalog(http, configuration, credentials, binding.connector_key)

    async def aclose(self) -> None:
        # Closing a local binding neither closes a borrowed client nor revokes the account.
        pass

    async def inspect(self) -> ConnectionInspection:
        value = required_object(
            await self._http.request(
                "GET",
                endpoint=self._configuration.endpoint,
                path=f"/api/v3.1/connected_accounts/{path_segment(self._binding.external_ref)}",
                api_key=self._credentials.api_key,
            )
        )
        return _inspection(
            value,
            external_ref=self._binding.external_ref,
            connector_key=self._binding.connector_key,
            external_user_correlation=self._binding.external_user_correlation,
        )

    async def revoke(
        self,
        *,
        operation_id: str,
    ) -> None:
        await self._http.request(
            "POST",
            endpoint=self._configuration.endpoint,
            path=f"/api/v3.1/connected_accounts/{path_segment(self._binding.external_ref)}/revoke",
            api_key=self._credentials.api_key,
            json_body={},
            write=True,
            extra_headers={"idempotency-key": operation_id},
        )

    async def discover_tools(self, *, cursor: str | None) -> ConnectorToolPage:
        return await self._catalog.discover_tools(cursor=cursor)

    async def execute_tool(
        self,
        *,
        tool_key: str,
        provider_version: str,
        arguments: JsonObject,
        request_id: str,
    ) -> ConnectorToolOutcome:
        if _TOOLKIT_VERSION.fullmatch(provider_version) is None:
            raise ConnectorProviderError("incompatible_toolkit_version")
        try:
            value = await self._http.request(
                "POST",
                endpoint=self._configuration.endpoint,
                path=f"/api/v3.1/tools/execute/{path_segment(tool_key)}",
                api_key=self._credentials.api_key,
                json_body={
                    "arguments": arguments,
                    "connected_account_id": self._binding.external_ref,
                    "version": provider_version,
                },
                write=True,
                extra_headers={"idempotency-key": request_id},
            )
        except ConnectorProviderError as error:
            if error.outcome_unknown:
                return ConnectorToolOutcome(kind="outcome_unknown", request_id=request_id)
            raise
        response = required_object(value)
        if response.get("successful") is not True:
            raise ConnectorProviderError("tool_rejected")
        return ConnectorToolOutcome(kind="succeeded", result=response.get("data"), request_id=request_id)


def _inspection(
    value: JsonObject,
    *,
    external_ref: str,
    connector_key: str,
    external_user_correlation: str,
) -> ConnectionInspection:
    if required_string(value, "id") != external_ref:
        raise ConnectorProviderError("connection_substitution")
    if required_string(value, "toolkit_slug", max_length=128) != connector_key:
        raise ConnectorProviderError("provider_mismatch")
    if required_string(value, "user_id", max_length=128) != external_user_correlation:
        raise ConnectorProviderError("owner_mismatch")
    status, reason = _status(required_string(value, "status", max_length=64))
    return ConnectionInspection(
        external_ref=external_ref,
        connector_key=connector_key,
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
        provider_version=required_string(value, "version", max_length=128),
        key=required_string(value, "slug", max_length=128),
        description=optional_string(value.get("description"), max_length=16_384) or "",
        input_schema=required_object(value.get("input_parameters")),
        output_schema=(
            required_object(value.get("output_parameters")) if value.get("output_parameters") is not None else None
        ),
        annotations={},
    )
