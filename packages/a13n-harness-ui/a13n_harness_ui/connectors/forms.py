"""The bounded form profile and write-only projection used by every Connector."""

from typing import Any

from pydantic import JsonValue


def safe_values(schema: dict[str, Any], values: dict[str, JsonValue]) -> dict[str, JsonValue]:
    def project(node: dict, value: JsonValue) -> JsonValue:
        if "$ref" in node:
            name = str(node["$ref"]).removeprefix("#/$defs/")
            node = schema.get("$defs", {}).get(name, {})
        if node.get("writeOnly"):
            return None
        if "anyOf" in node:
            alternatives = [item for item in node["anyOf"] if item.get("type") != "null"]
            if value is not None and len(alternatives) == 1:
                return project(alternatives[0], value)
        if isinstance(value, dict):
            properties = node.get("properties", {})
            return {
                key: project(properties[key], item)
                for key, item in value.items()
                if key in properties and not properties[key].get("writeOnly")
            }
        if isinstance(value, list):
            return [project(node.get("items", {}), item) for item in value]
        return value

    result = project(schema, values)
    return result if isinstance(result, dict) else {}


def validate_form(schema: dict[str, Any]) -> None:
    """Reject unsupported structure rather than replacing it with a raw JSON editor."""

    def visit(node: dict, references: frozenset[str] = frozenset()) -> None:
        reference = node.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
                raise ValueError("Only local form references are supported")
            if reference in references:
                raise ValueError("Recursive form references are unsupported")
            target = schema.get("$defs", {}).get(reference.removeprefix("#/$defs/"))
            if not isinstance(target, dict):
                raise ValueError("Missing local form reference")
            visit(target, references | {reference})
        if any(key in node for key in ("oneOf", "allOf", "if", "then", "else", "prefixItems")):
            raise ValueError("Unsupported conditional or tuple form schema")
        if isinstance(node.get("additionalProperties"), dict):
            raise ValueError("Repeatable object rows are required instead of open dictionary forms")
        if node.get("writeOnly") and node.get("default") not in (None, ""):
            raise ValueError("Write-only fields cannot publish a default value")
        alternatives = node.get("anyOf", [])
        if alternatives and (len(alternatives) != 2 or sum(child.get("type") == "null" for child in alternatives) != 1):
            raise ValueError("Only nullable form unions are supported")
        for child in alternatives:
            visit(child, references)
        for child in node.get("properties", {}).values():
            visit(child, references)
        if isinstance(node.get("items"), dict):
            visit(node["items"], references)

    visit(schema)
