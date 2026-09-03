"""Canonical validation for authorization-isolated MCP tool catalogs."""

from __future__ import annotations

from functools import partial

from anyio import to_thread
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import TypeAdapter, ValidationError

from a13n_service.connectivity.bounds import (
    CATALOG_MAX_BYTES,
    JSON_MAX_DEPTH,
    TOOL_DESCRIPTION_MAX_BYTES,
    TOOL_NAME_MAX_BYTES,
    TOOL_SCHEMA_MAX_BYTES,
)
from a13n_service.connectivity.ingress.domain import JsonObject
from a13n_service.connectivity.management import canonical_json

from .domain import MCP_PROTOCOL_REVISION, MCPTool
from .protocol import MCPDiscovery, MCPProtocolError

_JSON_OBJECT = TypeAdapter(JsonObject)


async def catalog_bytes(
    *,
    connection_id: str,
    credential_generation: int,
    discovery: MCPDiscovery,
) -> bytes:
    return await to_thread.run_sync(
        partial(
            _catalog_bytes,
            connection_id=connection_id,
            credential_generation=credential_generation,
            discovery=discovery,
        )
    )


def _catalog_bytes(*, connection_id: str, credential_generation: int, discovery: MCPDiscovery) -> bytes:
    if discovery.protocol_revision != MCP_PROTOCOL_REVISION:
        raise MCPProtocolError("incompatible_protocol")
    seen: set[str] = set()
    ordered = sorted(discovery.tools, key=lambda item: item.name)
    _require_depth(discovery.capabilities)
    for tool in ordered:
        _validate_tool(tool, seen)
    value = {
        "schema_version": "1",
        "mcp_connection_id": connection_id,
        "protocol_revision": discovery.protocol_revision,
        "credential_generation": credential_generation,
        "server": {"name": discovery.server_name, "version": discovery.server_version},
        "capabilities": discovery.capabilities,
        "tools": [tool.model_dump(mode="json") for tool in ordered],
    }
    try:
        body = canonical_json(_JSON_OBJECT.validate_python(value)).encode()
    except ValidationError as error:
        raise MCPProtocolError("invalid_tool_catalog") from error
    if not body or len(body) > CATALOG_MAX_BYTES:
        raise MCPProtocolError("catalog_too_large")
    return body


def _validate_tool(tool: MCPTool, seen: set[str]) -> None:
    if tool.name in seen:
        raise MCPProtocolError("duplicate_tool_name")
    seen.add(tool.name)
    if not tool.name or len(tool.name.encode()) > TOOL_NAME_MAX_BYTES:
        raise MCPProtocolError("catalog_too_large")
    if len(tool.description.encode()) > TOOL_DESCRIPTION_MAX_BYTES:
        raise MCPProtocolError("catalog_too_large")
    schemas = canonical_json({"input": tool.input_schema, "output": tool.output_schema}).encode()
    if len(schemas) > TOOL_SCHEMA_MAX_BYTES:
        raise MCPProtocolError("catalog_too_large")
    for value in (tool.input_schema, tool.output_schema):
        if value is None:
            continue
        if value.get("type") != "object":
            raise MCPProtocolError("invalid_tool_schema")
        _require_depth(value)
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError as error:
            raise MCPProtocolError("invalid_tool_schema") from error
    _require_depth(tool.annotations)


def _require_depth(value: object) -> None:
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > JSON_MAX_DEPTH:
            raise MCPProtocolError("catalog_too_deep")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
