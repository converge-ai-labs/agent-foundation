"""Composio v3.1 ConnectorProvider adapter."""

from __future__ import annotations

from datetime import datetime

from a13n_harness.providers.connector.configuration import ApiKeyCredentials
from a13n_harness.providers.connector.contracts import JsonObject

from ..contracts import (
    AdapterConnectionStatus,
    AdapterStatusReason,
    BeforeDispatch,
    BeforeSharedSetup,
    ConnectionBinding,
    ConnectionInspection,
    ConnectorProviderError,
    ConnectorToolOutcome,
    ConnectorToolPage,
    DiscoveredConnector,
    ProviderAccess,
    SetupCompletionMethod,
    SetupContext,
    SetupStarted,
)
from ..http import ConnectorHttpClient
from ..validation import (
    optional_string,
    path_segment,
    provider_response_errors,
    required_object,
    required_string,
    same_origin_url,
)
from .catalog import TOOLKIT_VERSION, ComposioCatalog, ComposioToolCatalog
from .configuration import COMPOSIO_CONNECT_ENDPOINT, COMPOSIO_ENDPOINT


class ComposioProvider:
    compatibility_profile = "composio_v3_1"
    setup_replay_safe = False

    def __init__(self, http: ConnectorHttpClient, credentials: ApiKeyCredentials) -> None:
        self._http = http
        # The Provider boundary is the one place the API key is revealed for outbound requests.
        self._api_key = credentials.api_key.get_secret_value()
        self._catalog = ComposioCatalog(http, self._api_key)

    def connect(self, binding: ConnectionBinding) -> ComposioConnection:
        return ComposioConnection(self._http, self._api_key, binding)

    def tool_catalog(self, connector_key: str, *, provider_version: str | None = None) -> ComposioToolCatalog:
        return ComposioToolCatalog(self._http, self._api_key, connector_key, provider_version=provider_version)

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
            api_key=self._api_key,
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
        credentials: JsonObject | None = None,
    ) -> SetupStarted:
        if resume_ref is not None:
            raise ConnectorProviderError("setup_replay_unavailable")
        if credentials is not None:
            return await self._create_with_credentials(
                setup=setup, credentials=credentials, context=context, before_shared_setup=before_shared_setup
            )
        if context.callback_url is None:
            raise ConnectorProviderError("callback_unavailable")
        auth_config = await self._catalog.prepare_setup(context.connector_key, setup, before_shared_setup)
        value = await self._http.request(
            "POST",
            endpoint=COMPOSIO_ENDPOINT,
            path="/api/v3.1/connected_accounts/link",
            api_key=self._api_key,
            json_body={
                "auth_config_id": auth_config.id,
                **({"connection_data": setup["connection_data"]} if setup.get("connection_data") else {}),
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
                expected_metadata={"auth_config_id": auth_config.id, "auth_scheme": auth_config.scheme},
                completion_method=SetupCompletionMethod.oauth_verifier
                if auth_config.scheme == "OAUTH2"
                else SetupCompletionMethod.browser_confirmation,
            )
        except ValueError as error:
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=True) from error

    async def _create_with_credentials(
        self,
        *,
        setup: JsonObject,
        credentials: JsonObject,
        context: SetupContext,
        before_shared_setup: BeforeSharedSetup | None,
    ) -> SetupStarted:
        auth_config = await self._catalog.prepare_credentials(
            context.connector_key, setup, credentials, before_shared_setup
        )
        data = setup.get("connection_data", {})
        if not isinstance(data, dict):
            raise ConnectorProviderError("invalid_setup_options")
        value = await self._http.request(
            "POST",
            endpoint=COMPOSIO_ENDPOINT,
            path="/api/v3.1/connected_accounts",
            api_key=self._api_key,
            json_body={
                "auth_config": {"id": auth_config.id},
                "connection": {
                    "user_id": context.external_user_correlation,
                    "state": {"authScheme": auth_config.scheme, "val": {**data, **credentials, "status": "ACTIVE"}},
                },
            },
            write=True,
        )
        try:
            identifier = required_string(required_object(value), "id")
        except ValueError as error:
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=True) from error
        return SetupStarted(
            setup_ref=identifier,
            external_ref=identifier,
            completion_method=SetupCompletionMethod.polling,
            expected_metadata={"auth_config_id": auth_config.id, "auth_scheme": auth_config.scheme},
        )

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
            api_key=self._api_key,
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
        try:
            return await self.inspect_setup(setup_ref=expected_external_ref, context=context)
        except ConnectorProviderError as error:
            # Redemption succeeded; a failed follow-up read cannot prove rejection.
            if error.code in {
                "provider_unavailable",
                "rate_limited",
                "provider_rejected",
                "response_too_large",
                "invalid_provider_response",
            }:
                raise ConnectorProviderError(error.code, outcome_unknown=True) from error
            raise


def _link_expiry(response: JsonObject) -> datetime:
    expiry = datetime.fromisoformat(required_string(response, "expires_at"))
    if expiry.tzinfo is None:
        raise ValueError("Auth link expiry must have a timezone")
    return expiry


def _authorization_url(value: object) -> str:
    # Connect Links are hosted separately from the REST API. Never accept a wildcard origin.
    if not isinstance(value, str):
        raise ValueError("Invalid authorization URL")
    return same_origin_url(value, endpoint=COMPOSIO_CONNECT_ENDPOINT)


class ComposioConnection:
    def __init__(
        self,
        http: ConnectorHttpClient,
        api_key: str,
        binding: ConnectionBinding,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._binding = binding
        self._catalog = ComposioToolCatalog(http, api_key, binding.connector_key)

    async def inspect(self) -> ConnectionInspection:
        with provider_response_errors():
            value = required_object(
                await self._http.request(
                    "GET",
                    endpoint=COMPOSIO_ENDPOINT,
                    path=f"/api/v3.1/connected_accounts/{path_segment(self._binding.external_ref)}",
                    api_key=self._api_key,
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
            api_key=self._api_key,
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
        before_dispatch: BeforeDispatch | None = None,
    ) -> ConnectorToolOutcome:
        if TOOLKIT_VERSION.fullmatch(provider_version) is None:
            raise ConnectorProviderError("incompatible_toolkit_version")
        if before_dispatch is not None:
            await before_dispatch()
        try:
            value = await self._http.request(
                "POST",
                endpoint=COMPOSIO_ENDPOINT,
                path=f"/api/v3.1/tools/execute/{path_segment(tool_key)}",
                api_key=self._api_key,
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
    auth_config = required_object(value.get("auth_config", {}))
    auth_disabled = auth_config.get("is_disabled", False)
    if not isinstance(auth_disabled, bool):
        raise ValueError("Invalid authentication configuration state")
    scheme = optional_string(auth_config.get("auth_scheme"), max_length=64)
    if value.get("authScheme") is not None and scheme is not None and value["authScheme"] != scheme:
        raise ConnectorProviderError("provider_mismatch")
    if disabled or auth_disabled:
        status, reason = AdapterConnectionStatus.disabled, None
    return ConnectionInspection(
        external_ref=external_ref,
        connector_key=connector_key,
        external_user_correlation=external_user_correlation,
        status=status,
        status_reason=reason,
        safe_metadata={
            "auth_config_id": optional_string(auth_config.get("id"), max_length=256),
            "auth_scheme": scheme,
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
