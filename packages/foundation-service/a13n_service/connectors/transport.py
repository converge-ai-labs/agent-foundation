"""Authenticated internal transport for Connector Provider operations."""

from __future__ import annotations

import base64
import hmac
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

import httpx2
from fastapi import FastAPI, Request
from pydantic import JsonValue, SecretStr
from starlette.responses import JSONResponse

from .errors import ConnectorError, ConnectorReauthorizationRequired
from .operations import ConnectorProviderOperations
from .provider import (
    ConnectorProviderAccount,
    ConnectorProviderCapabilities,
    ConnectorProviderConnection,
    ConnectorProviderConnectionResult,
    ConnectorProviderContext,
    ConnectorProviderEvent,
    ConnectorProviderEventSourceResult,
    ConnectorProviderEventType,
    ConnectorProviderMetadata,
    ConnectorProviderSecret,
    ConnectorProviderSetupResult,
    ConnectorProviderTool,
)
from .registry import ConnectorProviderRegistration

logger = logging.getLogger("a13n_service.connectors.transport")

_MAX_BODY_BYTES = 2 * 1024 * 1024
_OPERATIONS_PATH = "/{provider_key}/{operation}"


def create_connector_provider_operations_app(
    operations: ConnectorProviderOperations,
    *,
    authentication_token: SecretStr,
) -> FastAPI:
    """Create the Connector-role-only internal Provider operations surface."""

    expected_token = authentication_token.get_secret_value()
    if len(expected_token) < 32:
        raise ValueError("Connector internal authentication token must contain at least 32 characters")
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.post(_OPERATIONS_PATH, include_in_schema=False)
    async def invoke_provider_operation(request: Request, provider_key: str, operation: str) -> JSONResponse:
        if not _authenticated(request, expected_token):
            return _error_response(
                ConnectorError("Connector Service authentication failed.", code="authentication_required"),
                status_code=401,
            )
        try:
            body = await _bounded_body(request)
        except ConnectorError as error:
            return _error_response(
                error,
                status_code=413,
            )
        try:
            value = json.loads(body)
            arguments = _json_object(value, field="request")
            result = await _dispatch(operations, provider_key, operation, arguments)
        except ConnectorError as error:
            return _error_response(error, status_code=_error_status(error.code))
        except Exception:
            logger.exception(
                "connector_provider_operation_failed",
                extra={"event": "connector_provider_operation_failed", "operation": operation},
            )
            return _error_response(
                ConnectorError("Connector Provider operation failed.", code="provider_operation_failed"),
                status_code=502,
            )
        return JSONResponse({"result": result})

    return app


async def _bounded_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > _MAX_BODY_BYTES:
                raise ConnectorError(
                    "Connector Provider operation payload is too large.",
                    code="invalid_request",
                )
        except ValueError:
            raise ConnectorError(
                "Connector Provider operation content length is invalid.",
                code="invalid_request",
            ) from None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_BODY_BYTES:
            raise ConnectorError(
                "Connector Provider operation payload is too large.",
                code="invalid_request",
            )
    return bytes(body)


class RemoteConnectorProviderOperations:
    """Control-role client for the authenticated Connector Service boundary."""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        self._client = client

    async def registrations(self) -> tuple[ConnectorProviderRegistration, ...]:
        result = await self._invoke("_catalog", "registrations", {})
        return tuple(_registration_from_wire(item) for item in _json_array(result, field="registrations"))

    async def metadata(self, provider_key: str) -> ConnectorProviderMetadata:
        return _metadata_from_wire(await self._invoke(provider_key, "metadata", {}))

    async def validate_config(
        self,
        provider_key: str,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
    ) -> None:
        await self._invoke(
            provider_key,
            "validate_config",
            {"provider_config_version": provider_config_version, "config": dict(config)},
        )

    async def connection_spec(
        self,
        provider_key: str,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
    ) -> Mapping[str, JsonValue]:
        return _json_object(
            await self._invoke(
                provider_key,
                "connection_spec",
                {"provider_config_version": provider_config_version, "config": dict(config)},
            ),
            field="connection_spec",
        )

    async def list_tools(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection | None,
    ) -> tuple[ConnectorProviderTool, ...]:
        result = await self._invoke(
            provider_key,
            "list_tools",
            {
                "context": _context_to_wire(context),
                "provider_config_version": provider_config_version,
                "config": dict(config),
                "connection": _connection_to_wire(connection) if connection is not None else None,
            },
            context=context,
        )
        return tuple(_tool_from_wire(item) for item in _json_array(result, field="tools"))

    async def list_events(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
    ) -> tuple[ConnectorProviderEventType, ...]:
        result = await self._invoke(
            provider_key,
            "list_events",
            {
                "context": _context_to_wire(context),
                "provider_config_version": provider_config_version,
                "config": dict(config),
                "connection": _connection_to_wire(connection),
            },
            context=context,
        )
        return tuple(_event_type_from_wire(item) for item in _json_array(result, field="events"))

    async def start_connection(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        setup_mode: str,
        input: Mapping[str, JsonValue],
        callback_url: str | None,
        callback_state: str | None,
    ) -> ConnectorProviderSetupResult:
        result = await self._invoke(
            provider_key,
            "start_connection",
            {
                "context": _context_to_wire(context),
                "provider_config_version": provider_config_version,
                "config": dict(config),
                "setup_mode": setup_mode,
                "input": dict(input),
                "callback_url": callback_url,
                "callback_state": callback_state,
            },
            context=context,
        )
        return _setup_result_from_wire(result)

    async def complete_connection(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        continuation_state: Mapping[str, JsonValue],
        input: Mapping[str, JsonValue],
    ) -> ConnectorProviderConnectionResult:
        result = await self._invoke(
            provider_key,
            "complete_connection",
            {
                "context": _context_to_wire(context),
                "provider_config_version": provider_config_version,
                "config": dict(config),
                "continuation_state": dict(continuation_state),
                "input": dict(input),
            },
            context=context,
        )
        return _connection_result_from_wire(result)

    async def refresh_connection(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
    ) -> ConnectorProviderConnectionResult:
        result = await self._invoke(
            provider_key,
            "refresh_connection",
            {
                "context": _context_to_wire(context),
                "provider_config_version": provider_config_version,
                "config": dict(config),
                "connection": _connection_to_wire(connection),
            },
            context=context,
        )
        return _connection_result_from_wire(result)

    async def revoke_connection(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
    ) -> None:
        await self._invoke(
            provider_key,
            "revoke_connection",
            {
                "context": _context_to_wire(context),
                "provider_config_version": provider_config_version,
                "config": dict(config),
                "connection": _connection_to_wire(connection),
            },
            context=context,
        )

    async def validate_connection(
        self,
        provider_key: str,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
    ) -> None:
        await self._invoke(
            provider_key,
            "validate_connection",
            {
                "provider_config_version": provider_config_version,
                "config": dict(config),
                "connection": _connection_to_wire(connection),
            },
        )

    async def validate_event_config(
        self,
        provider_key: str,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
        event_type: str,
        provider_event_config_version: str,
        event_config: Mapping[str, JsonValue],
    ) -> None:
        await self._invoke(
            provider_key,
            "validate_event_config",
            {
                "provider_config_version": provider_config_version,
                "config": dict(config),
                "connection": _connection_to_wire(connection),
                "event_type": event_type,
                "provider_event_config_version": provider_event_config_version,
                "event_config": dict(event_config),
            },
        )

    async def start_event_source(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
        event_type: str,
        provider_event_config_version: str,
        event_config: Mapping[str, JsonValue],
        callback_url: str | None,
    ) -> ConnectorProviderEventSourceResult:
        result = await self._invoke(
            provider_key,
            "start_event_source",
            {
                "context": _context_to_wire(context),
                "provider_config_version": provider_config_version,
                "config": dict(config),
                "connection": _connection_to_wire(connection),
                "event_type": event_type,
                "provider_event_config_version": provider_event_config_version,
                "event_config": dict(event_config),
                "callback_url": callback_url,
            },
            context=context,
        )
        return _event_source_from_wire(result)

    async def stop_event_source(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
    ) -> None:
        await self._invoke(
            provider_key,
            "stop_event_source",
            {"context": _context_to_wire(context), "source": _event_source_to_wire(source)},
            context=context,
        )

    async def renew_event_source(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
    ) -> ConnectorProviderEventSourceResult:
        result = await self._invoke(
            provider_key,
            "renew_event_source",
            {"context": _context_to_wire(context), "source": _event_source_to_wire(source)},
            context=context,
        )
        return _event_source_from_wire(result)

    async def reconcile_event_source(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
        event_type: str,
        provider_event_config_version: str,
        event_config: Mapping[str, JsonValue],
        source: ConnectorProviderEventSourceResult | None,
    ) -> ConnectorProviderEventSourceResult:
        result = await self._invoke(
            provider_key,
            "reconcile_event_source",
            {
                "context": _context_to_wire(context),
                "provider_config_version": provider_config_version,
                "config": dict(config),
                "connection": _connection_to_wire(connection),
                "event_type": event_type,
                "provider_event_config_version": provider_event_config_version,
                "event_config": dict(event_config),
                "source": _event_source_to_wire(source) if source is not None else None,
            },
            context=context,
        )
        return _event_source_from_wire(result)

    async def receive_webhook(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[ConnectorProviderEvent, ...]:
        result = await self._invoke(
            provider_key,
            "receive_webhook",
            {
                "context": _context_to_wire(context),
                "source": _event_source_to_wire(source),
                "headers": dict(headers),
                "body_base64": base64.b64encode(body).decode("ascii"),
            },
            context=context,
        )
        return tuple(_event_from_wire(item) for item in _json_array(result, field="events"))

    async def poll_events(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
        cursor: str | None,
    ) -> tuple[tuple[ConnectorProviderEvent, ...], str | None]:
        result = _json_object(
            await self._invoke(
                provider_key,
                "poll_events",
                {
                    "context": _context_to_wire(context),
                    "source": _event_source_to_wire(source),
                    "cursor": cursor,
                },
                context=context,
            ),
            field="poll result",
        )
        events = tuple(_event_from_wire(item) for item in _json_array(result.get("events"), field="events"))
        return events, _optional_string(result.get("cursor"), field="cursor")

    async def _invoke(
        self,
        provider_key: str,
        operation: str,
        arguments: Mapping[str, JsonValue],
        *,
        context: ConnectorProviderContext | None = None,
    ) -> JsonValue:
        timeout = None
        if context is not None:
            timeout = max((context.deadline.astimezone(UTC) - datetime.now(UTC)).total_seconds(), 0.001)
        try:
            response = await self._client.post(
                f"/internal/connector-provider-operations/{provider_key}/{operation}",
                json=dict(arguments),
                timeout=timeout,
            )
        except httpx2.HTTPError:
            raise ConnectorError("Connector Service is unavailable.", code="dependency_unavailable") from None
        try:
            payload = _json_object(response.json(), field="response")
        except Exception:
            raise ConnectorError(
                "Connector Service returned an invalid response.", code="dependency_unavailable"
            ) from None
        if response.is_error:
            error = _json_object(payload.get("error"), field="error")
            code = _string(error.get("code"), field="error.code")
            message = _string(error.get("message"), field="error.message")
            details = _json_object(error.get("details", {}), field="error.details")
            if code == "connection_reauthorization_required":
                raise ConnectorReauthorizationRequired()
            raise ConnectorError(message, code=code, details=details)
        return cast(JsonValue, payload.get("result"))


async def _dispatch(
    operations: ConnectorProviderOperations,
    provider_key: str,
    operation: str,
    values: Mapping[str, JsonValue],
) -> JsonValue:
    if operation == "registrations" and provider_key == "_catalog":
        return cast(JsonValue, [_registration_to_wire(item) for item in await operations.registrations()])
    if operation == "metadata":
        return cast(JsonValue, _metadata_to_wire(await operations.metadata(provider_key)))
    if operation == "validate_config":
        await operations.validate_config(
            provider_key,
            provider_config_version=_string(values.get("provider_config_version"), field="provider_config_version"),
            config=_json_object(values.get("config"), field="config"),
        )
        return None
    if operation == "connection_spec":
        result = await operations.connection_spec(
            provider_key,
            provider_config_version=_string(values.get("provider_config_version"), field="provider_config_version"),
            config=_json_object(values.get("config"), field="config"),
        )
        return cast(JsonValue, dict(result))

    context = _context_from_wire(values.get("context")) if "context" in values else None
    config_version = _optional_string(values.get("provider_config_version"), field="provider_config_version")
    config = _json_object(values.get("config"), field="config") if "config" in values else None
    connection_value = values.get("connection")
    connection = _connection_from_wire(connection_value) if connection_value is not None else None

    if operation == "list_tools":
        tools = await operations.list_tools(
            provider_key,
            _required_context(context),
            provider_config_version=_required_string(config_version, "provider_config_version"),
            config=_required_object(config, "config"),
            connection=connection,
        )
        return cast(JsonValue, [_tool_to_wire(item) for item in tools])
    if operation == "list_events":
        events = await operations.list_events(
            provider_key,
            _required_context(context),
            provider_config_version=_required_string(config_version, "provider_config_version"),
            config=_required_object(config, "config"),
            connection=_required_connection(connection),
        )
        return cast(JsonValue, [_event_type_to_wire(item) for item in events])

    if operation == "start_connection":
        result = await operations.start_connection(
            provider_key,
            _required_context(context),
            provider_config_version=_required_string(config_version, "provider_config_version"),
            config=_required_object(config, "config"),
            setup_mode=_string(values.get("setup_mode"), field="setup_mode"),
            input=_json_object(values.get("input"), field="input"),
            callback_url=_optional_string(values.get("callback_url"), field="callback_url"),
            callback_state=_optional_string(values.get("callback_state"), field="callback_state"),
        )
        return cast(JsonValue, _setup_result_to_wire(result))
    if operation == "complete_connection":
        result = await operations.complete_connection(
            provider_key,
            _required_context(context),
            provider_config_version=_required_string(config_version, "provider_config_version"),
            config=_required_object(config, "config"),
            continuation_state=_json_object(values.get("continuation_state"), field="continuation_state"),
            input=_json_object(values.get("input"), field="input"),
        )
        return cast(JsonValue, _connection_result_to_wire(result))
    if operation == "refresh_connection":
        result = await operations.refresh_connection(
            provider_key,
            _required_context(context),
            provider_config_version=_required_string(config_version, "provider_config_version"),
            config=_required_object(config, "config"),
            connection=_required_connection(connection),
        )
        return cast(JsonValue, _connection_result_to_wire(result))
    if operation == "revoke_connection":
        await operations.revoke_connection(
            provider_key,
            _required_context(context),
            provider_config_version=_required_string(config_version, "provider_config_version"),
            config=_required_object(config, "config"),
            connection=_required_connection(connection),
        )
        return None
    if operation == "validate_connection":
        await operations.validate_connection(
            provider_key,
            provider_config_version=_required_string(config_version, "provider_config_version"),
            config=_required_object(config, "config"),
            connection=_required_connection(connection),
        )
        return None
    if operation == "validate_event_config":
        await operations.validate_event_config(
            provider_key,
            provider_config_version=_required_string(config_version, "provider_config_version"),
            config=_required_object(config, "config"),
            connection=_required_connection(connection),
            event_type=_string(values.get("event_type"), field="event_type"),
            provider_event_config_version=_string(
                values.get("provider_event_config_version"), field="provider_event_config_version"
            ),
            event_config=_json_object(values.get("event_config"), field="event_config"),
        )
        return None

    source = _event_source_from_wire(values.get("source")) if values.get("source") is not None else None
    if operation == "start_event_source":
        result = await operations.start_event_source(
            provider_key,
            _required_context(context),
            provider_config_version=_required_string(config_version, "provider_config_version"),
            config=_required_object(config, "config"),
            connection=_required_connection(connection),
            event_type=_string(values.get("event_type"), field="event_type"),
            provider_event_config_version=_string(
                values.get("provider_event_config_version"), field="provider_event_config_version"
            ),
            event_config=_json_object(values.get("event_config"), field="event_config"),
            callback_url=_optional_string(values.get("callback_url"), field="callback_url"),
        )
        return cast(JsonValue, _event_source_to_wire(result))
    if operation == "stop_event_source":
        await operations.stop_event_source(
            provider_key,
            _required_context(context),
            source=_required_source(source),
        )
        return None
    if operation == "renew_event_source":
        result = await operations.renew_event_source(
            provider_key,
            _required_context(context),
            source=_required_source(source),
        )
        return cast(JsonValue, _event_source_to_wire(result))
    if operation == "reconcile_event_source":
        result = await operations.reconcile_event_source(
            provider_key,
            _required_context(context),
            provider_config_version=_required_string(config_version, "provider_config_version"),
            config=_required_object(config, "config"),
            connection=_required_connection(connection),
            event_type=_string(values.get("event_type"), field="event_type"),
            provider_event_config_version=_string(
                values.get("provider_event_config_version"), field="provider_event_config_version"
            ),
            event_config=_json_object(values.get("event_config"), field="event_config"),
            source=source,
        )
        return cast(JsonValue, _event_source_to_wire(result))
    if operation == "receive_webhook":
        try:
            body = base64.b64decode(_string(values.get("body_base64"), field="body_base64"), validate=True)
        except ValueError:
            raise ConnectorError("Connector Provider operation payload is invalid.", code="invalid_request") from None
        events = await operations.receive_webhook(
            provider_key,
            _required_context(context),
            source=_required_source(source),
            headers=_string_mapping(values.get("headers"), field="headers"),
            body=body,
        )
        return cast(JsonValue, [_event_to_wire(item) for item in events])
    if operation == "poll_events":
        events, cursor = await operations.poll_events(
            provider_key,
            _required_context(context),
            source=_required_source(source),
            cursor=_optional_string(values.get("cursor"), field="cursor"),
        )
        return cast(JsonValue, {"events": [_event_to_wire(item) for item in events], "cursor": cursor})
    raise ConnectorError("Connector Provider operation is unsupported.", code="invalid_request")


def _authenticated(request: Request, expected_token: str) -> bool:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and bool(token) and hmac.compare_digest(token, expected_token)


def _error_response(error: ConnectorError, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": error.code, "message": str(error), "details": dict(error.details)}},
        status_code=status_code,
    )


def _error_status(code: str) -> int:
    if code in {"provider_unavailable", "dependency_unavailable", "provider_timeout"}:
        return 503
    if code == "not_found":
        return 404
    return 400


def _registration_to_wire(value: ConnectorProviderRegistration) -> dict[str, JsonValue]:
    return {
        "provider_key": value.provider_key,
        "class_module": value.class_module,
        "class_qualname": value.class_qualname,
        "import_target": value.import_target,
        "distribution_name": value.distribution_name,
        "distribution_version": value.distribution_version,
        "metadata": _metadata_to_wire(value.metadata),
    }


def _registration_from_wire(value: JsonValue) -> ConnectorProviderRegistration:
    item = _json_object(value, field="registration")
    return ConnectorProviderRegistration(
        provider_key=_string(item.get("provider_key"), field="provider_key"),
        class_module=_string(item.get("class_module"), field="class_module"),
        class_qualname=_string(item.get("class_qualname"), field="class_qualname"),
        import_target=_string(item.get("import_target"), field="import_target"),
        distribution_name=_string(item.get("distribution_name"), field="distribution_name"),
        distribution_version=_string(item.get("distribution_version"), field="distribution_version"),
        metadata=_metadata_from_wire(item.get("metadata")),
    )


def _metadata_to_wire(value: ConnectorProviderMetadata) -> dict[str, JsonValue]:
    return {
        "display_name": value.display_name,
        "description": value.description,
        "contract_version": value.contract_version,
        "provider_config_schemas": {key: dict(schema) for key, schema in value.provider_config_schemas.items()},
        "capabilities": {
            "tools": value.capabilities.tools,
            "connections": value.capabilities.connections,
            "events": value.capabilities.events,
            "event_delivery": value.capabilities.event_delivery,
        },
        "connection_setup_modes": list(value.connection_setup_modes),
    }


def _metadata_from_wire(value: JsonValue) -> ConnectorProviderMetadata:
    item = _json_object(value, field="metadata")
    capabilities = _json_object(item.get("capabilities"), field="capabilities")
    schemas = _json_object(item.get("provider_config_schemas"), field="provider_config_schemas")
    return ConnectorProviderMetadata(
        display_name=_string(item.get("display_name"), field="display_name"),
        description=_string(item.get("description"), field="description"),
        contract_version=_string(item.get("contract_version"), field="contract_version"),
        provider_config_schemas={
            key: _json_object(schema, field=f"provider_config_schemas.{key}") for key, schema in schemas.items()
        },
        capabilities=ConnectorProviderCapabilities(
            tools=_boolean(capabilities.get("tools"), field="tools"),
            connections=_boolean(capabilities.get("connections"), field="connections"),
            events=_boolean(capabilities.get("events"), field="events"),
            event_delivery=cast(Any, _optional_string(capabilities.get("event_delivery"), field="event_delivery")),
        ),
        connection_setup_modes=tuple(
            _string(mode, field="connection_setup_mode")
            for mode in _json_array(item.get("connection_setup_modes"), field="connection_setup_modes")
        ),
    )


def _context_to_wire(value: ConnectorProviderContext) -> dict[str, JsonValue]:
    return {"operation_id": value.operation_id, "deadline": value.deadline.astimezone(UTC).isoformat()}


def _context_from_wire(value: JsonValue) -> ConnectorProviderContext:
    item = _json_object(value, field="context")
    return ConnectorProviderContext(
        operation_id=_string(item.get("operation_id"), field="operation_id"),
        deadline=_datetime(item.get("deadline"), field="deadline"),
    )


def _secrets_to_wire(values: tuple[ConnectorProviderSecret, ...]) -> list[JsonValue]:
    return cast(
        list[JsonValue],
        [{"key": item.key, "value": item.value.get_secret_value()} for item in values],
    )


def _secrets_from_wire(value: JsonValue) -> tuple[ConnectorProviderSecret, ...]:
    return tuple(
        ConnectorProviderSecret(
            key=_string(item.get("key"), field="secret.key"),
            value=SecretStr(_string(item.get("value"), field="secret.value")),
        )
        for raw in _json_array(value, field="secrets")
        for item in (_json_object(raw, field="secret"),)
    )


def _connection_to_wire(value: ConnectorProviderConnection) -> dict[str, JsonValue]:
    return {
        "provider_state_version": value.provider_state_version,
        "provider_state": dict(value.provider_state),
        "secrets": _secrets_to_wire(value.secrets),
    }


def _connection_from_wire(value: JsonValue) -> ConnectorProviderConnection:
    item = _json_object(value, field="connection")
    return ConnectorProviderConnection(
        provider_state_version=_string(item.get("provider_state_version"), field="provider_state_version"),
        provider_state=_json_object(item.get("provider_state"), field="provider_state"),
        secrets=_secrets_from_wire(item.get("secrets")),
    )


def _connection_result_to_wire(value: ConnectorProviderConnectionResult) -> dict[str, JsonValue]:
    return {
        "provider_state_version": value.provider_state_version,
        "provider_state": dict(value.provider_state),
        "account": {"external_id": value.account.external_id, "display_name": value.account.display_name},
        "secrets": _secrets_to_wire(value.secrets),
        "expires_at": value.expires_at.astimezone(UTC).isoformat() if value.expires_at is not None else None,
    }


def _connection_result_from_wire(value: JsonValue) -> ConnectorProviderConnectionResult:
    item = _json_object(value, field="connection_result")
    account = _json_object(item.get("account"), field="account")
    expires_at = item.get("expires_at")
    return ConnectorProviderConnectionResult(
        provider_state_version=_string(item.get("provider_state_version"), field="provider_state_version"),
        provider_state=_json_object(item.get("provider_state"), field="provider_state"),
        account=ConnectorProviderAccount(
            external_id=_string(account.get("external_id"), field="account.external_id"),
            display_name=_string(account.get("display_name"), field="account.display_name"),
        ),
        secrets=_secrets_from_wire(item.get("secrets")),
        expires_at=_datetime(expires_at, field="expires_at") if expires_at is not None else None,
    )


def _setup_result_to_wire(value: ConnectorProviderSetupResult) -> dict[str, JsonValue]:
    return {
        "completed": value.completed,
        "next_action": dict(value.next_action) if value.next_action is not None else None,
        "continuation_state": dict(value.continuation_state) if value.continuation_state is not None else None,
        "connection": _connection_result_to_wire(value.connection) if value.connection is not None else None,
    }


def _setup_result_from_wire(value: JsonValue) -> ConnectorProviderSetupResult:
    item = _json_object(value, field="setup_result")
    connection = item.get("connection")
    return ConnectorProviderSetupResult(
        completed=_boolean(item.get("completed"), field="completed"),
        next_action=(
            _json_object(item.get("next_action"), field="next_action") if item.get("next_action") is not None else None
        ),
        continuation_state=(
            _json_object(item.get("continuation_state"), field="continuation_state")
            if item.get("continuation_state") is not None
            else None
        ),
        connection=_connection_result_from_wire(connection) if connection is not None else None,
    )


def _event_source_to_wire(value: ConnectorProviderEventSourceResult) -> dict[str, JsonValue]:
    return {
        "provider_state_version": value.provider_state_version,
        "provider_state": dict(value.provider_state),
        "secrets": _secrets_to_wire(value.secrets),
    }


def _event_source_from_wire(value: JsonValue) -> ConnectorProviderEventSourceResult:
    item = _json_object(value, field="event_source")
    return ConnectorProviderEventSourceResult(
        provider_state_version=_string(item.get("provider_state_version"), field="provider_state_version"),
        provider_state=_json_object(item.get("provider_state"), field="provider_state"),
        secrets=_secrets_from_wire(item.get("secrets")),
    )


def _event_to_wire(value: ConnectorProviderEvent) -> dict[str, JsonValue]:
    return {
        "event_id": value.event_id,
        "event_type": value.event_type,
        "data": dict(value.data),
        "occurred_at": value.occurred_at.astimezone(UTC).isoformat() if value.occurred_at is not None else None,
    }


def _event_from_wire(value: JsonValue) -> ConnectorProviderEvent:
    item = _json_object(value, field="event")
    occurred_at = item.get("occurred_at")
    return ConnectorProviderEvent(
        event_id=_string(item.get("event_id"), field="event_id"),
        event_type=_string(item.get("event_type"), field="event_type"),
        data=_json_object(item.get("data"), field="data"),
        occurred_at=_datetime(occurred_at, field="occurred_at") if occurred_at is not None else None,
    )


def _tool_to_wire(value: ConnectorProviderTool) -> dict[str, JsonValue]:
    return {
        "name": value.name,
        "tool_id": value.tool_id,
        "description": value.description,
        "parameters_json_schema": dict(value.parameters_json_schema),
        "effects": list(value.effects),
        "credential_audiences": list(value.credential_audiences),
        "idempotency": value.idempotency,
        "output_policy": dict(value.output_policy),
    }


def _tool_from_wire(value: JsonValue) -> ConnectorProviderTool:
    item = _json_object(value, field="tool")
    return ConnectorProviderTool(
        name=_string(item.get("name"), field="name"),
        tool_id=_string(item.get("tool_id"), field="tool_id"),
        description=_string(item.get("description"), field="description"),
        parameters_json_schema=_json_object(item.get("parameters_json_schema"), field="parameters_json_schema"),
        effects=tuple(_string(effect, field="effect") for effect in _json_array(item.get("effects"), field="effects")),
        credential_audiences=tuple(
            _string(audience, field="credential_audience")
            for audience in _json_array(item.get("credential_audiences"), field="credential_audiences")
        ),
        idempotency=_string(item.get("idempotency"), field="idempotency"),
        output_policy=_json_object(item.get("output_policy"), field="output_policy"),
    )


def _event_type_to_wire(value: ConnectorProviderEventType) -> dict[str, JsonValue]:
    return {
        "name": value.name,
        "description": value.description,
        "config_schema": dict(value.config_schema),
    }


def _event_type_from_wire(value: JsonValue) -> ConnectorProviderEventType:
    item = _json_object(value, field="event_type")
    return ConnectorProviderEventType(
        name=_string(item.get("name"), field="name"),
        description=_string(item.get("description"), field="description"),
        config_schema=_json_object(item.get("config_schema"), field="config_schema"),
    )


def _json_object(value: object, *, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConnectorError(f"{field} must be a JSON object.", code="invalid_request")
    return cast(dict[str, JsonValue], value)


def _json_array(value: object, *, field: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ConnectorError(f"{field} must be a JSON array.", code="invalid_request")
    return cast(list[JsonValue], value)


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ConnectorError(f"{field} must be a string.", code="invalid_request")
    return value


def _optional_string(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field=field)


def _boolean(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConnectorError(f"{field} must be a boolean.", code="invalid_request")
    return value


def _datetime(value: object, *, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(_string(value, field=field))
    except ValueError:
        raise ConnectorError(f"{field} must be an ISO 8601 datetime.", code="invalid_request") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ConnectorError(f"{field} must include a timezone.", code="invalid_request")
    return result


def _string_mapping(value: object, *, field: str) -> dict[str, str]:
    item = _json_object(value, field=field)
    return {key: _string(child, field=f"{field}.{key}") for key, child in item.items()}


def _required_context(value: ConnectorProviderContext | None) -> ConnectorProviderContext:
    if value is None:
        raise ConnectorError("context is required.", code="invalid_request")
    return value


def _required_string(value: str | None, field: str) -> str:
    if value is None:
        raise ConnectorError(f"{field} is required.", code="invalid_request")
    return value


def _required_object(value: dict[str, JsonValue] | None, field: str) -> dict[str, JsonValue]:
    if value is None:
        raise ConnectorError(f"{field} is required.", code="invalid_request")
    return value


def _required_connection(value: ConnectorProviderConnection | None) -> ConnectorProviderConnection:
    if value is None:
        raise ConnectorError("connection is required.", code="invalid_request")
    return value


def _required_source(value: ConnectorProviderEventSourceResult | None) -> ConnectorProviderEventSourceResult:
    if value is None:
        raise ConnectorError("source is required.", code="invalid_request")
    return value


__all__ = [
    "RemoteConnectorProviderOperations",
    "create_connector_provider_operations_app",
]
