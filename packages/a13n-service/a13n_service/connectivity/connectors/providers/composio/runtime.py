"""Composio v3.1 ConnectorProvider adapter."""

from __future__ import annotations

from datetime import datetime

from anyio import Semaphore, create_task_group

from a13n_service.connectivity.connectors.providers.configuration import ApiKeyCredentials
from a13n_service.connectivity.domain import JsonObject

from ...contracts import (
    AdapterConnectionStatus,
    AdapterStatusReason,
    BeforeDispatch,
    BeforeSharedSetup,
    ConnectionBinding,
    ConnectionInspection,
    ConnectorProviderError,
    ConnectorTool,
    ConnectorToolOutcome,
    ConnectorToolPage,
    DiscoveredConnector,
    ProviderAccess,
    SetupCompletionMethod,
    SetupContext,
    SetupStarted,
)
from ...http import ConnectorHttpClient
from ...validation import (
    optional_string,
    path_segment,
    provider_response_errors,
    required_object,
    required_string,
    same_origin_url,
)
from .catalog import TOOLKIT_VERSION, ComposioCatalog
from .configuration import COMPOSIO_ENDPOINT
from .output_schema import corrected_output_schema


class ComposioProvider:
    compatibility_profile = "composio_v3_1"
    setup_replay_safe = False

    def __init__(self, http: ConnectorHttpClient, credentials: ApiKeyCredentials) -> None:
        self._http = http
        self._credentials = credentials
        self._catalog = ComposioCatalog(http, credentials.api_key)

    async def aclose(self) -> None:
        # The process owns the shared HTTP client.
        pass

    def connect(self, binding: ConnectionBinding) -> ComposioConnection:
        return ComposioConnection(self._http, self._credentials, binding)

    def tool_catalog(self, connector_key: str) -> ComposioToolCatalog:
        return ComposioToolCatalog(self._http, self._credentials, connector_key)

    async def inspect_setup(self, *, setup_ref: str, context: SetupContext) -> ConnectionInspection:
        return await self.connect(
            ConnectionBinding(
                external_ref=setup_ref,
                connector_key=context.connector_key,
                external_user_correlation=context.external_user_correlation,
            )
        ).inspect()

    async def test(self) -> tuple[ProviderAccess, ...]:
        await self._http.request(
            "GET",
            endpoint=COMPOSIO_ENDPOINT,
            path="/api/v3.1/connected_accounts?limit=1",
            api_key=self._credentials.api_key,
        )
        return ("account_read",)

    async def discover_connectors(self) -> tuple[DiscoveredConnector, ...]:
        return await self._catalog.directory()

    async def discover_connector(self, connector_key: str) -> DiscoveredConnector:
        return await self._catalog.connector(connector_key)

    async def start_setup(
        self,
        *,
        setup: JsonObject,
        context: SetupContext,
        resume_ref: str | None = None,
        before_shared_setup: BeforeSharedSetup | None = None,
    ) -> SetupStarted:
        if resume_ref is not None:
            raise ConnectorProviderError("setup_replay_unavailable")
        if context.callback_url is None:
            raise ConnectorProviderError("callback_unavailable")
        auth_config = await self._catalog.prepare_setup(context.connector_key, setup, before_shared_setup)
        value = await self._http.request(
            "POST",
            endpoint=COMPOSIO_ENDPOINT,
            path="/api/v3.1/connected_accounts/link",
            api_key=self._credentials.api_key,
            json_body={
                "auth_config_id": auth_config.id,
                "callback_url": context.callback_url,
                "user_id": context.external_user_correlation,
            },
            write=True,
        )
        try:
            response = required_object(value)
            return SetupStarted(
                setup_ref=required_string(response, "connected_account_id"),
                external_ref=required_string(response, "connected_account_id"),
                expires_at=_link_expiry(response),
                redirect_url=_authorization_url(response.get("redirect_url")),
                completion_method=SetupCompletionMethod.oauth_verifier
                if auth_config.scheme == "OAUTH2"
                else SetupCompletionMethod.browser_confirmation,
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
            endpoint=COMPOSIO_ENDPOINT,
            path="/api/v3.1/connected_accounts/complete_auth",
            api_key=self._credentials.api_key,
            json_body={
                "session_uri": session_uri,
                "user_id": context.external_user_correlation,
            },
            write=True,
        )
        try:
            response = required_object(value)
            if (
                required_string(response, "connected_account_id") != expected_external_ref
                or required_string(response, "toolkit_slug") != context.connector_key
            ):
                raise ConnectorProviderError("connection_substitution")
        except ValueError as error:
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=True) from error
        return await self.inspect_setup(setup_ref=expected_external_ref, context=context)


def _link_expiry(response: JsonObject) -> datetime:
    expiry = datetime.fromisoformat(required_string(response, "expires_at"))
    if expiry.tzinfo is None:
        raise ValueError("Auth link expiry must have a timezone")
    return expiry


def _authorization_url(value: object) -> str:
    # Connect Links are hosted separately from the REST API. Never accept a wildcard origin.
    if not isinstance(value, str):
        raise ValueError("Invalid authorization URL")
    return same_origin_url(value, endpoint="https://connect.composio.dev")


class ComposioToolCatalog:
    """Read a toolkit's definitions without an external account binding."""

    def __init__(
        self,
        http: ConnectorHttpClient,
        credentials: ApiKeyCredentials,
        connector_key: str,
    ) -> None:
        self._http = http
        self._credentials = credentials
        self._connector_key = connector_key
        self._catalog_version: str | None = None

    async def discover_tools(self, *, cursor: str | None) -> ConnectorToolPage:
        if self._catalog_version is None:
            toolkit = required_object(
                await self._http.request(
                    "GET",
                    endpoint=COMPOSIO_ENDPOINT,
                    path=f"/api/v3.1/toolkits/{path_segment(self._connector_key)}",
                    api_key=self._credentials.api_key,
                )
            )
            if required_string(toolkit, "slug", max_length=128) != self._connector_key:
                raise ConnectorProviderError("provider_mismatch")
            version = required_string(required_object(toolkit.get("meta")), "version", max_length=128)
            if TOOLKIT_VERSION.fullmatch(version) is None:
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
                endpoint=COMPOSIO_ENDPOINT,
                path="/api/v3.1/tools",
                api_key=self._credentials.api_key,
                params=params,
            )
        )
        items = value.get("items")
        if not isinstance(items, list) or len(items) > 100:
            raise ConnectorProviderError("invalid_provider_response")
        entries = [required_object(item) for item in items]
        keys = [required_string(item, "slug", max_length=128) for item in entries]
        if len(keys) != len(set(keys)):
            raise ConnectorProviderError("invalid_provider_response")
        tools: dict[str, ConnectorTool] = {}
        limit = Semaphore(32)

        async def load(key: str, entry: JsonObject) -> None:
            # Current directory pages already carry complete, versioned schemas.
            # Sparse directory entries still need the individual detail endpoint.
            if {"toolkit", "version", "input_parameters", "output_parameters"} <= entry.keys():
                tools[key] = parse_detail(key, entry)
            else:
                async with limit:
                    tools[key] = await detail_for(key)

        async def detail_for(key: str) -> ConnectorTool:
            detail = required_object(
                await self._http.request(
                    "GET",
                    endpoint=COMPOSIO_ENDPOINT,
                    path=f"/api/v3.1/tools/{path_segment(key)}",
                    api_key=self._credentials.api_key,
                    params={"version": version},
                )
            )
            return parse_detail(key, detail)

        def parse_detail(key: str, detail: JsonObject) -> ConnectorTool:
            if (
                required_string(detail, "slug", max_length=128) != key
                or required_string(required_object(detail.get("toolkit")), "slug", max_length=128)
                != self._connector_key
                or required_string(detail, "version", max_length=128) != version
            ):
                raise ConnectorProviderError("incompatible_tool_version")
            return _tool(detail)

        try:
            async with create_task_group() as group:
                for key, entry in zip(keys, entries, strict=True):
                    group.start_soon(load, key, entry)
        except* (ConnectorProviderError, ValueError) as failures:
            raise failures.exceptions[0] from None
        return ConnectorToolPage(
            items=tuple(tools[key] for key in keys),
            next_cursor=optional_string(value.get("next_cursor")),
            provider_version=version,
        )


class ComposioConnection:
    def __init__(
        self,
        http: ConnectorHttpClient,
        credentials: ApiKeyCredentials,
        binding: ConnectionBinding,
    ) -> None:
        self._http = http
        self._credentials = credentials
        self._binding = binding
        self._catalog = ComposioToolCatalog(http, credentials, binding.connector_key)

    async def aclose(self) -> None:
        # Closing a local binding neither closes a borrowed client nor revokes the account.
        pass

    async def inspect(self) -> ConnectionInspection:
        with provider_response_errors():
            value = required_object(
                await self._http.request(
                    "GET",
                    endpoint=COMPOSIO_ENDPOINT,
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
            endpoint=COMPOSIO_ENDPOINT,
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
        before_dispatch: BeforeDispatch,
    ) -> ConnectorToolOutcome:
        if TOOLKIT_VERSION.fullmatch(provider_version) is None:
            raise ConnectorProviderError("incompatible_toolkit_version")
        await before_dispatch()
        try:
            value = await self._http.request(
                "POST",
                endpoint=COMPOSIO_ENDPOINT,
                path=f"/api/v3.1/tools/execute/{path_segment(tool_key)}",
                api_key=self._credentials.api_key,
                json_body={
                    "arguments": arguments,
                    "connected_account_id": self._binding.external_ref,
                    "user_id": self._binding.external_user_correlation,
                    "version": provider_version,
                },
                write=True,
                extra_headers={"idempotency-key": request_id},
            )
        except ConnectorProviderError as error:
            if error.outcome_unknown:
                return ConnectorToolOutcome(kind="outcome_unknown", request_id=request_id)
            raise
        if not isinstance(value, dict) or type(value.get("successful")) is not bool:
            return ConnectorToolOutcome(kind="outcome_unknown", request_id=request_id)
        response = value
        if response["successful"] is False:
            data = response.get("data")
            status = data.get("status_code") if isinstance(data, dict) else None
            # Execution can return HTTP 200 with an authoritative upstream refusal.
            # Never expose the accompanying free-form error or request details.
            http_status = status if type(status) is int and status in {401, 403, 404, 429} else None
            raise ConnectorProviderError(
                "rate_limited" if http_status == 429 else "tool_rejected", http_status=http_status
            )
        # Upstream emits null for no error, but declares error as an optional string.
        if response.get("error") is None:
            response.pop("error", None)
        # Composio output_parameters describes the entire execution response.
        return ConnectorToolOutcome(kind="succeeded", result=response, request_id=request_id)


def _inspection(
    value: JsonObject,
    *,
    external_ref: str,
    connector_key: str,
    external_user_correlation: str,
) -> ConnectionInspection:
    if required_string(value, "id") != external_ref:
        raise ConnectorProviderError("connection_substitution")
    if required_string(required_object(value.get("toolkit")), "slug", max_length=128) != connector_key:
        raise ConnectorProviderError("provider_mismatch")
    if required_string(value, "user_id", max_length=128) != external_user_correlation:
        raise ConnectorProviderError("owner_mismatch")
    status, reason = _status(required_string(value, "status", max_length=64))
    disabled = value.get("is_disabled", False)
    if not isinstance(disabled, bool):
        raise ValueError("Invalid account enabled state")
    if disabled:
        status, reason = AdapterConnectionStatus.disabled, None
    return ConnectionInspection(
        external_ref=external_ref,
        connector_key=connector_key,
        external_user_correlation=external_user_correlation,
        status=status,
        status_reason=reason,
        safe_metadata={
            "display_name": optional_string(value.get("alias"), max_length=256),
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
    key = required_string(value, "slug", max_length=128)
    version = required_string(value, "version", max_length=128)
    return ConnectorTool(
        provider_version=version,
        key=key,
        description=optional_string(value.get("description"), max_length=16_384) or "",
        input_schema=required_object(value.get("input_parameters")),
        output_schema=(
            corrected_output_schema(required_object(value["output_parameters"]), tool_key=key, version=version)
            if value.get("output_parameters") is not None
            else None
        ),
        annotations={},
    )
