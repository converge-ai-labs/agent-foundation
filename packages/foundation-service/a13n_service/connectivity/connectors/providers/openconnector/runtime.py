"""OpenConnector native v1 adapter."""

from __future__ import annotations

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
from .configuration import OpenConnectorConfiguration, OpenConnectorSetup


class OpenConnectorProvider:
    compatibility_profile = "openconnector_native_v1"

    def __init__(
        self,
        http: ConnectorHttpClient,
        configuration: OpenConnectorConfiguration,
        credentials: ApiKeyCredentials,
    ) -> None:
        self._http = http
        self._configuration = configuration
        self._credentials = credentials

    async def aclose(self) -> None:
        # The process owns the shared HTTP client.
        pass

    def connect(self, binding: ConnectionBinding) -> OpenConnectorConnection:
        if binding.connector_key not in self._configuration.enabled_provider_slugs:
            raise ConnectorProviderError("connector_not_enabled")
        return OpenConnectorConnection(self._http, self._configuration, self._credentials, binding)

    async def test(
        self,
    ) -> None:
        await self._http.request(
            "GET",
            endpoint=self._configuration.endpoint,
            path="/api/v1/connectors/connections?limit=1",
            api_key=self._credentials.api_key,
        )

    async def discover_connectors(self) -> tuple[DiscoveredConnector, ...]:
        budget = DirectoryBudget()
        toolkits = await directory_items(
            self._http,
            endpoint=self._configuration.endpoint,
            api_key=self._credentials.api_key,
            path="/api/v1/toolkits",
            budget=budget,
            pagination="nextCursor",
        )
        auth_configs = await directory_items(
            self._http,
            endpoint=self._configuration.endpoint,
            api_key=self._credentials.api_key,
            path="/api/v1/auth_configs",
            budget=budget,
            pagination="offset",
        )
        configured: dict[str, list[tuple[str, str]]] = {}
        for item in auth_configs:
            if item.get("disabled") is not False or item.get("authMethodKind") != "oauth2":
                continue
            key = required_string(item, "toolkitSlug", max_length=128)
            configured.setdefault(key, []).append(
                (required_string(item, "id", max_length=256), required_string(item, "authMethodId", max_length=128))
            )
        result: list[DiscoveredConnector] = []
        seen: set[str] = set()
        for item in toolkits:
            key = required_string(item, "slug", max_length=128)
            if key in seen:
                raise ConnectorProviderError("invalid_provider_response")
            seen.add(key)
            if key not in self._configuration.enabled_provider_slugs:
                continue
            methods = item.get("authMethods")
            if not isinstance(methods, list) or len(methods) > 32:
                raise ConnectorProviderError("invalid_provider_response")
            available = {
                required_string(method, "id", max_length=128)
                for value in methods
                if (method := required_object(value)).get("kind") == "oauth2" and method.get("status") == "available"
            }
            ids = [identifier for identifier, method in configured.get(key, []) if method in available]
            result.append(
                DiscoveredConnector(
                    key=key,
                    name=required_string(item, "name", max_length=128),
                    description=optional_string(item.get("description"), max_length=16_384),
                    setup_schema=setup_schema(ids) if ids else {"not": {}},
                    authentication_methods=("oauth2",) if ids else (),
                )
            )
        return tuple(result)

    async def start_setup(
        self,
        *,
        setup: JsonObject,
        context: SetupContext,
    ) -> SetupStarted:
        configured = OpenConnectorSetup.model_validate(setup)
        await validate_discovered_setup(self.discover_connectors, context.connector_key, setup)
        value = await self._http.request(
            "POST",
            endpoint=self._configuration.endpoint,
            path=f"/api/v1/connectors/{path_segment(context.connector_key)}/initiate",
            api_key=self._credentials.api_key,
            json_body={
                "authConfigId": configured.auth_config_id,
                "userId": context.external_user_correlation,
            },
            write=True,
            extra_headers={"idempotency-key": context.attempt_id},
        )
        try:
            response = required_object(value)
            return SetupStarted(
                external_ref=required_string(response, "connectionId"),
                redirect_url=same_origin_url(response.get("redirectUrl"), endpoint=self._configuration.endpoint),
                supports_verified_callback=False,
            )
        except ValueError as error:
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=True) from error

    async def complete_setup(
        self, *, session_uri: str, context: SetupContext, expected_external_ref: str
    ) -> ConnectionInspection:
        raise ConnectorProviderError("callback_not_supported")


class OpenConnectorConnection:
    def __init__(
        self,
        http: ConnectorHttpClient,
        configuration: OpenConnectorConfiguration,
        credentials: ApiKeyCredentials,
        binding: ConnectionBinding,
    ) -> None:
        self._http = http
        self._configuration = configuration
        self._credentials = credentials
        self._binding = binding

    async def aclose(self) -> None:
        # Closing a local binding neither closes a borrowed client nor revokes the account.
        pass

    async def inspect(self) -> ConnectionInspection:
        value = await self._http.request(
            "GET",
            endpoint=self._configuration.endpoint,
            path=f"/api/v1/connectors/connections/{path_segment(self._binding.external_ref)}",
            api_key=self._credentials.api_key,
        )
        response = required_object(value)
        return _inspection(
            response,
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
            "DELETE",
            endpoint=self._configuration.endpoint,
            path=f"/api/v1/connectors/connections/{path_segment(self._binding.external_ref)}",
            api_key=self._credentials.api_key,
            write=True,
            extra_headers={"idempotency-key": operation_id},
        )

    async def discover_tools(self, *, cursor: str | None) -> ConnectorToolPage:
        params = {"toolkit": self._binding.connector_key, "limit": "100"}
        if cursor is not None:
            params["cursor"] = cursor
        value = required_object(
            await self._http.request(
                "GET",
                endpoint=self._configuration.endpoint,
                path="/api/v1/tools",
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
            detail = await self._tool_detail(key)
            if required_string(detail, "version", max_length=128) != required_string(
                summary, "version", max_length=128
            ):
                raise ConnectorProviderError("incompatible_tool_version")
            tools.append(_tool(detail))
        return ConnectorToolPage(
            items=tuple(tools), next_cursor=optional_string(value.get("nextCursor")), provider_version="native_v1"
        )

    async def _tool_detail(self, tool_key: str) -> JsonObject:
        detail = required_object(
            await self._http.request(
                "GET",
                endpoint=self._configuration.endpoint,
                path=f"/api/v1/tools/{path_segment(tool_key)}",
                api_key=self._credentials.api_key,
            )
        )
        if (
            required_string(detail, "slug", max_length=128) != tool_key
            or required_string(detail, "toolkit", max_length=128) != self._binding.connector_key
        ):
            raise ConnectorProviderError("provider_mismatch")
        return detail

    async def execute_tool(
        self,
        *,
        tool_key: str,
        provider_version: str,
        arguments: JsonObject,
        request_id: str,
    ) -> ConnectorToolOutcome:
        detail = await self._tool_detail(tool_key)
        if required_string(detail, "version", max_length=128) != provider_version:
            raise ConnectorProviderError("incompatible_tool_version")
        try:
            value = await self._http.request(
                "POST",
                endpoint=self._configuration.endpoint,
                path=f"/api/v1/tools/{path_segment(tool_key)}/execute",
                api_key=self._credentials.api_key,
                json_body={
                    "connectedAccountId": self._binding.external_ref,
                    "userId": self._binding.external_user_correlation,
                    "arguments": arguments,
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
        status = response.get("status")
        if type(status) is not int or not 200 <= status < 300:
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
    if required_string(value, "toolkitSlug", max_length=128) != connector_key:
        raise ConnectorProviderError("provider_mismatch")
    if required_string(value, "userId", max_length=128) != external_user_correlation:
        raise ConnectorProviderError("owner_mismatch")
    status, reason = _status(required_string(value, "status", max_length=64), value.get("disabled"))
    return ConnectionInspection(
        external_ref=external_ref,
        connector_key=connector_key,
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
        provider_version=required_string(value, "version", max_length=128),
        key=required_string(value, "slug", max_length=128),
        description=optional_string(value.get("description"), max_length=16_384) or "",
        input_schema=input_schema,
        output_schema=required_object(output) if output is not None else None,
        annotations={},
    )
