"""Bounded Streamable HTTP discovery for the accepted MCP revision."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx2
from pydantic import TypeAdapter, ValidationError

from a13n_service.connectivity.bounds import CATALOG_MAX_BYTES, CATALOG_MAX_PAGES, CATALOG_MAX_TOOLS, MAX_REDIRECTS
from a13n_service.connectivity.http import (
    BoundedHttpResponse,
    ConnectivityHttpError,
    cookie_free_bounded_request,
)
from a13n_service.connectivity.ingress.domain import JsonObject
from a13n_service.connectivity.outbound_policy import EndpointPolicy, EndpointPolicyError

from .domain import MCP_PROTOCOL_REVISION, MCPTool

_JSON_OBJECT = TypeAdapter(JsonObject)
_ACCEPT = "application/json, text/event-stream"
_MAX_SESSION_ID_BYTES = 1024
_MAX_SSE_EVENTS = 4096
_MAX_SSE_EVENT_BYTES = 1024 * 1024
_ALLOWED_NOTIFICATIONS = frozenset(
    {
        "notifications/cancelled",
        "notifications/message",
        "notifications/progress",
    }
)


class MCPProtocolError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class MCPDiscovery:
    protocol_revision: str
    server_name: str
    server_version: str
    capabilities: JsonObject
    tools: tuple[MCPTool, ...]


class MCPProtocolClient:
    """Expose catalog discovery without exposing arbitrary JSON-RPC calls."""

    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        endpoint_policy: EndpointPolicy,
        *,
        response_max_bytes: int = CATALOG_MAX_BYTES,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self._http = http_client
        self._policy = endpoint_policy
        self._response_max_bytes = min(response_max_bytes, CATALOG_MAX_BYTES)
        self._max_redirects = min(max_redirects, MAX_REDIRECTS)

    async def discover(self, endpoint: str, *, credential_headers: dict[str, str] | None = None) -> MCPDiscovery:
        canonical = await self._validate_endpoint(endpoint)
        headers = credential_headers or {}
        initialize, response, initialize_bytes = await self._rpc(
            canonical,
            request_id=1,
            method="initialize",
            params={
                "protocolVersion": MCP_PROTOCOL_REVISION,
                "capabilities": {},
                "clientInfo": {"name": "agent-foundation", "version": "1"},
            },
            session_id=None,
            credential_headers=headers,
            post_initialize=False,
        )
        session_id = _session_id(response)
        try:
            return await self._discover_initialized(
                canonical,
                initialize=initialize,
                session_id=session_id,
                credential_headers=headers,
                bytes_used=initialize_bytes,
            )
        finally:
            if session_id is not None:
                await self._terminate(canonical, session_id=session_id, credential_headers=headers)

    async def _discover_initialized(
        self,
        endpoint: str,
        *,
        initialize: dict[str, Any],
        session_id: str | None,
        credential_headers: dict[str, str],
        bytes_used: int,
    ) -> MCPDiscovery:
        revision = initialize.get("protocolVersion")
        server_info = initialize.get("serverInfo")
        capabilities = initialize.get("capabilities")
        if revision != MCP_PROTOCOL_REVISION or not isinstance(server_info, dict) or not isinstance(capabilities, dict):
            raise MCPProtocolError("incompatible_protocol")
        server_name = server_info.get("name")
        server_version = server_info.get("version")
        if (
            not isinstance(server_name, str)
            or not server_name
            or len(server_name.encode()) > 128
            or not isinstance(server_version, str)
            or len(server_version.encode()) > 128
        ):
            raise MCPProtocolError("invalid_server_info")
        try:
            safe_capabilities = _JSON_OBJECT.validate_python(capabilities)
        except ValidationError as error:
            raise MCPProtocolError("invalid_server_capabilities") from error

        await self._notification(
            endpoint,
            method="notifications/initialized",
            session_id=session_id,
            credential_headers=credential_headers,
        )
        tools: list[MCPTool] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        total_bytes = bytes_used
        for page in range(CATALOG_MAX_PAGES):
            request_id = page + 2
            params: dict[str, Any] = {} if cursor is None else {"cursor": cursor}
            remaining_bytes = self._response_max_bytes - total_bytes
            if remaining_bytes <= 0:
                raise MCPProtocolError("catalog_too_large")
            result, _, response_bytes = await self._rpc(
                endpoint,
                request_id=request_id,
                method="tools/list",
                params=params,
                session_id=session_id,
                credential_headers=credential_headers,
                post_initialize=True,
                max_bytes=remaining_bytes,
            )
            total_bytes += response_bytes
            raw_tools = result.get("tools")
            if not isinstance(raw_tools, list):
                raise MCPProtocolError("invalid_tool_catalog")
            tools.extend(_tool(item) for item in raw_tools)
            if len(tools) > CATALOG_MAX_TOOLS:
                raise MCPProtocolError("catalog_too_large")
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                break
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                raise MCPProtocolError("invalid_catalog_cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise MCPProtocolError("catalog_too_large")
        return MCPDiscovery(
            protocol_revision=MCP_PROTOCOL_REVISION,
            server_name=server_name,
            server_version=server_version,
            capabilities=safe_capabilities,
            tools=tuple(tools),
        )

    async def _rpc(
        self,
        endpoint: str,
        *,
        request_id: int,
        method: str,
        params: dict[str, Any],
        session_id: str | None,
        credential_headers: dict[str, str],
        post_initialize: bool,
        max_bytes: int | None = None,
    ) -> tuple[dict[str, Any], BoundedHttpResponse, int]:
        response = await self._send(
            endpoint,
            method="POST",
            headers=_headers(session_id, credential_headers, post_initialize=post_initialize),
            json_body={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            max_bytes=max_bytes if max_bytes is not None else self._response_max_bytes,
        )
        payloads, response_bytes = _response_payloads(response)
        matching: dict[str, Any] | None = None
        for payload in payloads:
            response_id = payload.get("id")
            if response_id is None and "method" in payload:
                notification_method = payload["method"]
                if isinstance(notification_method, str) and notification_method in _ALLOWED_NOTIFICATIONS:
                    continue
                raise MCPProtocolError("incompatible_notification")
            if type(response_id) is not int or response_id != request_id or matching is not None:
                raise MCPProtocolError("invalid_response_id")
            matching = payload
        if matching is None:
            raise MCPProtocolError("missing_response")
        has_result = "result" in matching
        has_error = "error" in matching
        if has_result == has_error:
            raise MCPProtocolError("invalid_jsonrpc_response")
        if has_error:
            raise MCPProtocolError("remote_jsonrpc_error")
        result = matching["result"]
        if not isinstance(result, dict):
            raise MCPProtocolError("invalid_jsonrpc_result")
        return result, response, response_bytes

    async def _notification(
        self,
        endpoint: str,
        *,
        method: str,
        session_id: str | None,
        credential_headers: dict[str, str],
    ) -> None:
        response = await self._send(
            endpoint,
            method="POST",
            headers=_headers(session_id, credential_headers, post_initialize=True),
            json_body={"jsonrpc": "2.0", "method": method},
        )
        if response.status_code not in {200, 202, 204}:
            raise MCPProtocolError("notification_rejected")

    async def _terminate(self, endpoint: str, *, session_id: str, credential_headers: dict[str, str]) -> None:
        try:
            await self._send(
                endpoint,
                method="DELETE",
                headers=_headers(session_id, credential_headers, post_initialize=True),
                json_body=None,
                accepted_statuses=frozenset(range(200, 500)),
            )
        except (MCPProtocolError, httpx2.HTTPError):
            return

    async def _send(
        self,
        endpoint: str,
        *,
        method: str,
        headers: dict[str, str],
        json_body: dict[str, Any] | None,
        accepted_statuses: frozenset[int] = frozenset({200, 202, 204}),
        max_bytes: int | None = None,
    ) -> BoundedHttpResponse:
        current = endpoint
        origin_headers = dict(headers)
        for redirect_count in range(self._max_redirects + 1):
            current = await self._validate_endpoint(current)
            request_headers = dict(origin_headers)
            try:
                response = await cookie_free_bounded_request(
                    self._http,
                    method,
                    current,
                    headers=request_headers,
                    max_bytes=max_bytes,
                    json_body=json_body,
                )
            except ConnectivityHttpError as error:
                raise MCPProtocolError(error.code) from error
            if response.status_code not in {301, 302, 303, 307, 308}:
                if response.status_code not in accepted_statuses:
                    if response.status_code in {401, 403}:
                        raise MCPProtocolError("authorization_required")
                    raise MCPProtocolError("remote_request_failed")
                return response
            if redirect_count == self._max_redirects:
                raise MCPProtocolError("too_many_redirects")
            location = response.headers.get("location")
            if location is None:
                raise MCPProtocolError("invalid_redirect")
            try:
                current, same_origin = await self._policy.validate_redirect(
                    current,
                    urljoin(current, location),
                    resolve_dns=True,
                )
            except EndpointPolicyError as error:
                raise MCPProtocolError("unsafe_endpoint") from error
            if not same_origin:
                raise MCPProtocolError("origin_change_redirect")
        raise MCPProtocolError("too_many_redirects")

    async def _validate_endpoint(self, endpoint: str) -> str:
        try:
            return await self._policy.validate(endpoint, resolve_dns=True)
        except EndpointPolicyError as error:
            raise MCPProtocolError("unsafe_endpoint") from error


def _response_payloads(response: BoundedHttpResponse) -> tuple[tuple[dict[str, Any], ...], int]:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    body = response.body
    if content_type == "application/json":
        return (_jsonrpc_object(body),), len(body)
    if content_type == "text/event-stream":
        return _sse_payloads(body), len(body)
    raise MCPProtocolError("unsupported_content_type")


def _sse_payloads(body: bytes) -> tuple[dict[str, Any], ...]:
    normalized = body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    events = normalized.split(b"\n\n")
    if len(events) > _MAX_SSE_EVENTS:
        raise MCPProtocolError("sse_too_large")
    payloads: list[dict[str, Any]] = []
    for event in events:
        if not event.strip():
            continue
        if len(event) > _MAX_SSE_EVENT_BYTES:
            raise MCPProtocolError("sse_too_large")
        data = b"\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith(b"data:"))
        if not data:
            continue
        payloads.append(_jsonrpc_object(data))
    if not payloads:
        raise MCPProtocolError("invalid_sse_response")
    return tuple(payloads)


def _jsonrpc_object(body: bytes) -> dict[str, Any]:
    try:
        value: Any = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise MCPProtocolError("invalid_json_response") from error
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
        raise MCPProtocolError("invalid_jsonrpc_response")
    return value


def _tool(value: Any) -> MCPTool:
    if not isinstance(value, dict):
        raise MCPProtocolError("invalid_tool_catalog")
    mapped = {
        "name": value.get("name"),
        "description": value.get("description", ""),
        "input_schema": value.get("inputSchema"),
        "output_schema": value.get("outputSchema"),
        "annotations": value.get("annotations", {}),
    }
    try:
        return MCPTool.model_validate(mapped)
    except ValidationError as error:
        raise MCPProtocolError("invalid_tool_catalog") from error


def _session_id(response: BoundedHttpResponse) -> str | None:
    value = response.headers.get("mcp-session-id")
    if value is None:
        return None
    if (
        not value
        or len(value.encode()) > _MAX_SESSION_ID_BYTES
        or any(ord(char) < 33 or ord(char) == 127 for char in value)
    ):
        raise MCPProtocolError("invalid_session_id")
    return value


def _headers(
    session_id: str | None,
    credential_headers: dict[str, str],
    *,
    post_initialize: bool = False,
) -> dict[str, str]:
    headers = {"Accept": _ACCEPT, "Content-Type": "application/json", **credential_headers}
    if post_initialize:
        headers["MCP-Protocol-Version"] = MCP_PROTOCOL_REVISION
    if session_id is not None:
        headers["MCP-Session-Id"] = session_id
    return headers
