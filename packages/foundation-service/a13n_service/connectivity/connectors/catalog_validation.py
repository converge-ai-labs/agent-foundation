"""Shared Connector catalog bounds and schema validation."""

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import canonical_json

from .contracts import ConnectorTool
from .errors import ConnectorError

MAX_PAGES = 128
MAX_TOOLS = 2_048
MAX_CATALOG_BYTES = 16 * 1024 * 1024
MAX_TOOL_NAME_BYTES = 128
MAX_DESCRIPTION_BYTES = 16 * 1024
MAX_SCHEMAS_BYTES = 256 * 1024
MAX_JSON_DEPTH = 64


def validate_catalog_tools(tools: list[ConnectorTool]) -> None:
    seen: set[str] = set()
    for tool in tools:
        if tool.key in seen:
            raise ConnectorError(
                "catalog_incompatible", "ConnectorProvider catalog contains duplicate tools.", status_code=409
            )
        seen.add(tool.key)
        if len(tool.key.encode()) > MAX_TOOL_NAME_BYTES or len(tool.description.encode()) > MAX_DESCRIPTION_BYTES:
            raise ConnectorError(
                "catalog_too_large", "ConnectorProvider tool metadata exceeds its limit.", status_code=409
            )
        schemas = canonical_json({"input": tool.input_schema, "output": tool.output_schema}).encode()
        if len(schemas) > MAX_SCHEMAS_BYTES:
            raise ConnectorError(
                "catalog_too_large", "ConnectorProvider tool schema exceeds its limit.", status_code=409
            )
        require_depth(tool.input_schema)
        check_schema(tool.input_schema)
        if tool.output_schema is not None:
            require_depth(tool.output_schema)
            check_schema(tool.output_schema)
        require_depth(tool.annotations)


def require_depth(value: object) -> None:
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise ConnectorError(
                "catalog_too_deep", "ConnectorProvider catalog exceeds its nesting limit.", status_code=409
            )
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def check_schema(value: JsonObject) -> None:
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as error:
        raise ConnectorError(
            "catalog_incompatible", "ConnectorProvider returned an invalid JSON Schema.", status_code=409
        ) from error
