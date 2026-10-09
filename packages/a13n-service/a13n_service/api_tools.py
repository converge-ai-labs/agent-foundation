"""OpenAPI-generated MCP tools dispatching to the original HTTP application.

FastMCP owns parameter parsing and serialization. This adapter keeps JSON bodies nested (including explicit
nulls), and exposes HTTP status and precondition metadata instead of discarding them.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from copy import deepcopy
from typing import Any

import httpx2
from fastapi.routing import APIRoute
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools import Tool, ToolResult
from fastmcp.utilities.openapi import HTTPRoute, parse_openapi_to_http_routes
from fastmcp.utilities.openapi.director import RequestDirector
from jsonschema_path import SchemaPath
from mcp.types import ToolAnnotations
from starlette.routing import Match
from starlette.types import ASGIApp, Scope

from a13n_service.infra.http import ErrorEnvelope

CONTEXT_HEADERS = frozenset({"authorization", "cookie", "x-workspace-id"})
RESULT_HEADERS = ("etag", "x-request-id", "retry-after")


def tool_name(operation_id: str) -> str:
    """Stable across operation ordering; never resolve collisions by traversal order."""
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", operation_id)
    if len(name) > 64:
        name = name[:51] + "_" + hashlib.sha256(operation_id.encode()).hexdigest()[:12]
    return name


def api_tools(app: ASGIApp, schema: dict[str, Any], base_url: str, routes: Sequence[APIRoute]) -> list[Tool]:
    selected = deepcopy(schema)
    selected["paths"] = {}
    admitted: set[tuple[str, str]] = set()
    for path, item in schema.get("paths", {}).items():
        operations = {
            method: operation
            for method, operation in item.items()
            if isinstance(operation, dict) and operation.get("x-a13n-mcp") is True
        }
        if operations:
            selected["paths"][path] = {**operations, "parameters": item.get("parameters", [])}
            admitted.update((path, method.upper()) for method in operations)
    operations = parse_openapi_to_http_routes(selected)
    if {(route.path, route.method) for route in operations} != admitted:
        raise ValueError("An admitted MCP operation could not be parsed")
    director = RequestDirector(SchemaPath.from_dict(selected))
    tools: list[Tool] = []
    names = {"search_documents"}
    for route in operations:
        if not route.operation_id:
            raise ValueError(f"MCP operation needs an operationId: {route.method} {route.path}")
        name = tool_name(route.operation_id)
        if not name or name in names:
            raise ValueError(f"Duplicate or empty MCP tool name: {name}")
        names.add(name)
        targets = [
            target for target in routes if target.path_format == route.path and route.method in (target.methods or ())
        ]
        if len(targets) != 1:
            raise ValueError(f"MCP operation must identify one HTTP route: {route.operation_id}")
        tools.append(ApiTool(app, director, route, base_url, name, routes, targets[0]))
    return tools


def _input_schema(route: HTTPRoute) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Keep transport parameters flat and the original JSON body nested."""
    parameters = deepcopy(route.flat_param_schema)
    properties = parameters.setdefault("properties", {})
    mappings = {}
    for key, mapping in route.parameter_map.items():
        if mapping["location"] == "body" or (
            mapping["location"] == "header" and mapping["openapi_name"].lower() in CONTEXT_HEADERS
        ):
            properties.pop(key, None)
            parameters["required"] = [item for item in parameters.get("required", []) if item != key]
        else:
            if mapping["location"] == "cookie":
                raise ValueError(f"MCP operations cannot use cookie parameters: {route.operation_id}")
            mappings[key] = mapping
    if "request_body" in properties:
        raise ValueError(f"MCP parameter collides with request_body: {route.operation_id}")
    if route.request_body:
        if set(route.request_body.content_schema) != {"application/json"}:
            raise ValueError(f"MCP operations must have JSON bodies: {route.operation_id}")
        properties["request_body"] = deepcopy(route.request_body.content_schema["application/json"])
        if route.request_body.required:
            parameters.setdefault("required", []).append("request_body")
    parameters["additionalProperties"] = False
    return parameters, mappings


def _output_schema(route: HTTPRoute) -> dict[str, Any]:
    """Describe the HTTP envelope, retaining response and Service error schemas."""
    error_schema = ErrorEnvelope.model_json_schema()
    definitions = {**route.response_schemas, **error_schema.pop("$defs", {})}
    bodies = [error_schema]
    for response in route.responses.values():
        if not response.content_schema:
            bodies.append({"type": "null"})
        elif set(response.content_schema) == {"application/json"}:
            bodies.append(response.content_schema["application/json"])
        else:
            raise ValueError(f"MCP operations must have JSON responses: {route.operation_id}")
    return {
        "type": "object",
        "properties": {
            "status": {"type": "integer"},
            "headers": {"type": "object", "additionalProperties": {"type": "string"}},
            "body": {"anyOf": bodies},
        },
        "required": ["status", "headers", "body"],
        "$defs": definitions,
    }


class ApiTool(Tool):
    def __init__(
        self,
        app: ASGIApp,
        director: RequestDirector,
        route: HTTPRoute,
        base_url: str,
        name: str,
        routes: Sequence[APIRoute],
        target: APIRoute,
    ):
        parameters, mappings = _input_schema(route)
        super().__init__(
            name=name,
            description=f"{route.method} {route.path}\n{route.description or route.summary or ''}\n"
            "Returns {status, headers, body}. Use headers.etag as If-Match for subsequent writes. "
            "Writes are not retried; check resource state after an unknown outcome.",
            parameters=parameters,
            output_schema=_output_schema(route),
            annotations=ToolAnnotations(read_only_hint=route.method == "GET", open_world_hint=True),
        )
        self._app = app
        self._routes = routes
        self._target = target
        self._director = director
        # Do not let body-field mappings consume same-named path/query/header arguments.
        self._route = route.model_copy(update={"parameter_map": mappings, "request_body": None})
        self._base_url = base_url

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        if arguments.keys() - self.parameters["properties"].keys():
            raise ToolError("Unknown operation arguments; use the names in the tool schema")
        for name, mapping in self._route.parameter_map.items():
            if mapping["location"] == "path":
                converter = self._target.param_convertors[mapping["openapi_name"]]
                if name not in arguments or not re.fullmatch(converter.regex, str(arguments[name])):
                    raise ToolError("Invalid path parameter for this HTTP operation")
        directed = self._director.build(
            self._route, {key: value for key, value in arguments.items() if key != "request_body"}, self._base_url
        )
        # ASGI decodes %2F. An ID containing a slash must not turn a resource read into an excluded
        # download (or another operation). Use the same ordered, declared routes and public matcher.
        scope: Scope = {"type": "http", "path": directed.url.path, "root_path": "", "method": directed.method}
        matched = next((route for route in self._routes if route.matches(scope)[0] == Match.FULL), None)
        if matched is not self._target:
            raise ToolError("Arguments resolve to a different HTTP operation")
        incoming = get_http_request()
        headers = dict(directed.headers)
        headers.pop("content-length", None)
        for name in ("authorization", "x-workspace-id"):
            if name in incoming.headers:
                headers[name] = incoming.headers[name]
        content = None
        if "request_body" in arguments:
            content = json.dumps(arguments["request_body"], allow_nan=False).encode()
            headers["content-type"] = "application/json"
        request = httpx2.Request(directed.method, directed.url, headers=headers, content=content)
        # No sockets or pool, no shared headers or cookie jar, no second application lifespan.
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self._app, raise_app_exceptions=False)
        ) as client:
            response = await client.send(request)
        return ToolResult(
            structured_content={
                "status": response.status_code,
                "headers": {name: response.headers[name] for name in RESULT_HEADERS if name in response.headers},
                "body": response.json() if response.content else None,
            },
            is_error=not 200 <= response.status_code < 300,
        )
