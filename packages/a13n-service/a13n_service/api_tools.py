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

from a13n_service.infra.http import MCP_ENTITY_TAG, ErrorEnvelope
from a13n_service.infra.platform import PLATFORM_METADATA, PlatformOperation
from a13n_service.infra.reads import tool_response

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
    error_schema = ErrorEnvelope.model_json_schema()
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
        tools.append(ApiTool(app, director, route, base_url, name, routes, targets[0], error_schema))
    return tools


def argument_name(header: str) -> str:
    """The model-facing name of a header parameter: lowercase snake_case, such as `If-Match` -> `if_match`."""
    return re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")


EXPECTED_VERSION = (
    'The version this write expects: the `headers.etag` of your read, such as `"sch_x:3"`, or the same tag without '
    "quotes, `{id}:{version}` (`{key}:{version}` for a model), such as `sch_x:3`. Quotes are added when missing. "
    "A stale version fails with 412 and the current one; an omitted version fails with 428."
)


def entity_tag(value: str) -> str:
    """The quoted HTTP entity tag for a version given with or without its quotes."""
    return value if len(value) >= 2 and value.startswith('"') and value.endswith('"') else f'"{value}"'


def _empty_object(schema: dict[str, Any], definitions: dict[str, Any]) -> bool:
    """Whether a JSON body schema admits only `{}`, such as a command that accepts no configuration."""
    if (reference := schema.get("$ref")) is not None:
        schema = definitions.get(reference.rsplit("/", 1)[-1], {})
    return (
        schema.get("type") == "object" and not schema.get("properties") and schema.get("additionalProperties") is False
    )


def _input_schema(route: HTTPRoute) -> tuple[dict[str, Any], dict[str, dict[str, str]], frozenset[str], bool]:
    """Keep transport parameters flat and the original JSON body nested; header arguments are snake_case.

    A header whose schema declares `MCP_ENTITY_TAG` takes that argument name and unquoted versions. A body that
    admits only `{}` is optional, and omission sends `{}`.
    """
    parameters = deepcopy(route.flat_param_schema)
    properties = parameters.setdefault("properties", {})
    transport = {}
    for key, mapping in route.parameter_map.items():
        if mapping["location"] == "body" or (
            mapping["location"] == "header" and mapping["openapi_name"].lower() in CONTEXT_HEADERS
        ):
            properties.pop(key, None)
            parameters["required"] = [item for item in parameters.get("required", []) if item != key]
        else:
            if mapping["location"] == "cookie":
                raise ValueError(f"MCP operations cannot use cookie parameters: {route.operation_id}")
            transport[key] = mapping
    # Rename only after body fields are gone, so a body field never shadows or swallows a renamed header.
    mappings: dict[str, dict[str, str]] = {}
    entity_tags = set()
    for key, mapping in transport.items():
        name = key
        if mapping["location"] == "header":
            if declared := properties[key].pop(MCP_ENTITY_TAG, None):
                if not re.fullmatch(r"[a-z][a-z0-9_]*", declared):
                    raise ValueError(f"MCP entity-tag argument must be snake_case: {route.operation_id}")
                name = declared
                entity_tags.add(name)
                properties[key]["description"] = EXPECTED_VERSION
            else:
                name = argument_name(mapping["openapi_name"])
            if not name:
                raise ValueError(f"MCP header parameter has no argument name: {route.operation_id}")
            if name != key:
                if name in properties:
                    raise ValueError(f"MCP argument {name} collides after normalization: {route.operation_id}")
                properties[name] = properties.pop(key)
                parameters["required"] = [name if item == key else item for item in parameters.get("required", [])]
        mappings[name] = mapping
    if "request_body" in properties:
        raise ValueError(f"MCP parameter collides with request_body: {route.operation_id}")
    if route.request_body:
        if set(route.request_body.content_schema) != {"application/json"}:
            raise ValueError(f"MCP operations must have JSON bodies: {route.operation_id}")
        body = deepcopy(route.request_body.content_schema["application/json"])
        properties["request_body"] = body
        empty_body = _empty_object(body, parameters.get("$defs", {}))
        if route.request_body.required and not empty_body:
            parameters.setdefault("required", []).append("request_body")
    else:
        empty_body = False
    parameters["additionalProperties"] = False
    return parameters, mappings, frozenset(entity_tags), empty_body


def _output_schema(route: HTTPRoute, error_schema: dict[str, Any]) -> dict[str, Any]:
    """Describe the HTTP envelope, retaining response and Service error schemas."""
    error_schema = deepcopy(error_schema)
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
        error_schema: dict[str, Any],
    ):
        parameters, mappings, entity_tags, empty_body = _input_schema(route)
        metadata = (target.openapi_extra or {}).get(PLATFORM_METADATA)
        operation = PlatformOperation.model_validate(metadata) if metadata is not None else None
        super().__init__(
            name=name,
            description=f"{route.method} {route.path}\n{route.description or route.summary or ''}\n"
            "Returns {status, headers, body}. Use headers.etag as expected_version for subsequent writes. "
            "Writes are not retried; check resource state after an unknown outcome.",
            parameters=parameters,
            output_schema=_output_schema(route, error_schema),
            annotations=ToolAnnotations(
                read_only_hint=operation.effect == "read" if operation else route.method == "GET",
                destructive_hint=operation.effect == "write" if operation else route.method != "GET",
                idempotent_hint=False,
                open_world_hint=True,
            ),
            meta={PLATFORM_METADATA: metadata} if metadata else None,
        )
        self._app = app
        self._routes = routes
        self._target = target
        self._director = director
        # Do not let body-field mappings consume same-named path/query/header arguments.
        self._route = route.model_copy(update={"parameter_map": mappings, "request_body": None})
        self._base_url = base_url
        self._entity_tags = entity_tags
        self._empty_body = empty_body

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        accepted = self.parameters["properties"].keys()
        if unknown := arguments.keys() - accepted:
            # Names only, never values; a caller on an outdated spelling sees the replacement.
            raise ToolError(
                f"Unknown operation arguments: {', '.join(sorted(key[:64] for key in unknown))}. "
                f"Use the names in the tool schema: {', '.join(sorted(accepted)) or '(none)'}"
            )
        for name, mapping in self._route.parameter_map.items():
            if mapping["location"] == "path":
                converter = self._target.param_convertors[mapping["openapi_name"]]
                if name not in arguments or not re.fullmatch(converter.regex, str(arguments[name])):
                    raise ToolError("Invalid path parameter for this HTTP operation")
        transport = {
            key: entity_tag(value) if key in self._entity_tags and isinstance(value, str) else value
            for key, value in arguments.items()
            if key != "request_body"
        }
        directed = self._director.build(self._route, transport, self._base_url)
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
        if "request_body" in arguments or self._empty_body:
            content = json.dumps(arguments.get("request_body", {}), allow_nan=False).encode()
            headers["content-type"] = "application/json"
        request = httpx2.Request(directed.method, directed.url, headers=headers, content=content)
        # No sockets or pool, no shared headers or cookie jar, no second application lifespan.
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self._app, raise_app_exceptions=False)
        ) as client:
            token = tool_response.set(True)
            try:
                response = await client.send(request)
            finally:
                tool_response.reset(token)
        return ToolResult(
            structured_content={
                "status": response.status_code,
                "headers": {name: response.headers[name] for name in RESULT_HEADERS if name in response.headers},
                "body": response.json() if response.content else None,
            },
            is_error=not 200 <= response.status_code < 300,
        )
