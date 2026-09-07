"""Bounds for untrusted external tool definitions and results."""

from collections.abc import Sequence

from jsonschema import Draft202012Validator
from mcp.types import Tool

from .bounds import (
    DISCOVERY_MAX_BYTES,
    DISCOVERY_MAX_TOOLS,
    JSON_MAX_DEPTH,
    TOOL_DESCRIPTION_MAX_BYTES,
    TOOL_NAME_MAX_BYTES,
    TOOL_RESULT_MAX_BYTES,
    TOOL_SCHEMA_MAX_BYTES,
)
from .management import canonical_json


def require_depth(value: object) -> None:
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > JSON_MAX_DEPTH:
            raise ValueError("tool_value_too_deep")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend((item, depth + 1) for item in current)


def validate_tools(tools: Sequence[Tool]) -> int:
    if len(tools) > DISCOVERY_MAX_TOOLS or len({tool.name for tool in tools}) != len(tools):
        raise ValueError("tool_discovery_invalid")
    size = 0
    for tool in tools:
        value = tool.model_dump(mode="json", exclude_none=True)
        require_depth(value)
        size += len(canonical_json(value).encode())
        if size > DISCOVERY_MAX_BYTES:
            raise ValueError("tool_discovery_too_large")
        if (
            len(tool.name.encode()) > TOOL_NAME_MAX_BYTES
            or len((tool.description or "").encode()) > TOOL_DESCRIPTION_MAX_BYTES
        ):
            raise ValueError("tool_definition_too_large")
        schemas = {"input": tool.inputSchema, "output": tool.outputSchema}
        if len(canonical_json(schemas).encode()) > TOOL_SCHEMA_MAX_BYTES:
            raise ValueError("tool_schema_too_large")
        for schema in schemas.values():
            if schema is not None:
                validate_schema(schema)

    return size


def validate_schema(schema: dict, *, require_object: bool = True) -> None:
    Draft202012Validator.check_schema(schema)
    if require_object and schema.get("type") != "object":
        raise ValueError("tool_schema_must_be_object")
    pending: list[tuple[object, int]] = [(schema, 1)]
    visited: set[tuple[int, int]] = set()
    while pending:
        value, depth = pending.pop()
        if depth > JSON_MAX_DEPTH:
            raise ValueError("tool_schema_reference_too_deep")
        identity = (id(value), depth)
        if identity in visited:
            continue
        visited.add(identity)
        if isinstance(value, dict):
            if "$dynamicRef" in value or "$recursiveRef" in value:
                raise ValueError("tool_schema_reference_invalid")
            reference = value.get("$ref")
            if reference is not None:
                if not isinstance(reference, str) or not reference.startswith("#/"):
                    raise ValueError("tool_schema_reference_invalid")
                target = schema
                try:
                    for key in reference[2:].split("/"):
                        decoded = key.replace("~1", "/").replace("~0", "~")
                        target = target[int(decoded)] if isinstance(target, list) else target[decoded]
                except (KeyError, TypeError, ValueError, IndexError) as error:
                    raise ValueError("tool_schema_reference_invalid") from error
                pending.append((target, depth + 1))
            pending.extend((child, depth + 1) for child in value.values() if isinstance(child, (dict, list)))
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)


def validate_result(value: object) -> None:
    require_depth(value)
    if len(canonical_json(value).encode()) > TOOL_RESULT_MAX_BYTES:
        raise ValueError("tool_result_too_large")
