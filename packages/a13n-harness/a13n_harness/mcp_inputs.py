"""Shared validation and responses for MCP human input."""

from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker, SchemaError, ValidationError
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator


class McpInputResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["accept", "decline", "cancel"]
    content: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def content_only_on_accept(self) -> McpInputResponse:
        if self.action != "accept" and self.content is not None:
            raise ValueError("Only an accepted form response can include content.")
        return self


class McpTaskInputRequest(BaseModel):
    """A pending input request. Its contents are untrusted server data."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    mode: Literal["form", "url"]
    message: str
    schema_: dict[str, JsonValue] | None = Field(default=None, alias="schema")
    url: str | None = None
    expires_at: AwareDatetime | None = None
    elicitation_id: str | None = None

    def validate_prompt(self) -> str | None:
        """Validate before exposing server data; return the URL host in URL mode."""
        if len(self.message) > 8192:
            raise ValueError("MCP input message is too large.")
        if self.mode == "form" and self.schema_ is not None and self.url is None:
            validate_schema(self.schema_)
            return None
        if self.mode == "url" and self.url is not None and self.schema_ is None:
            return url_host(self.url)
        raise ValueError("MCP input has no valid form or URL destination.")

    def validate_response(self, response: McpInputResponse) -> None:
        if response.action != "accept":
            return
        self.validate_prompt()
        if self.mode == "url":
            if response.content is not None:
                raise ValueError("URL confirmation cannot include form data.")
            return
        schema = self.schema_ or {}
        properties = schema.get("properties", {})
        if response.content is None or not isinstance(properties, dict) or set(response.content) - properties.keys():
            raise ValueError("Form content is missing or contains unknown fields.")
        if not _fits(response.content):
            raise ValueError("MCP input response is too large.")
        _validate_value(schema, response.content)


def _fits(value: object) -> bool:
    try:
        return len(json.dumps(value, allow_nan=False).encode()) <= 64 * 1024
    except (ValueError, RecursionError):
        return False


def url_host(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or any(char.isspace() or ord(char) < 32 or char == "\\" for char in url)
        or "%" in host
    ):
        raise ValueError("MCP input URL must be an HTTP(S) browser destination without credentials.")
    _ = parsed.port
    return host


def _validate_value(schema: dict[str, Any], value: JsonValue) -> None:
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except ValidationError as exc:
        field = next(iter(exc.absolute_path), None)
        raise ValueError(
            f"Invalid value for {field}." if field is not None else "Missing or invalid MCP form values."
        ) from exc


def validate_field(schema: dict[str, JsonValue], value: JsonValue) -> None:
    _validate_value(schema, value)


def validate_schema(schema: dict[str, Any]) -> None:
    """Accept the renderable primitive subset, not arbitrary or remote JSON Schema."""
    if not _fits(schema):
        raise ValueError("MCP input schema is too large.")
    if schema.get("type") != "object" or not isinstance(schema.get("properties", {}), dict):
        raise ValueError("MCP forms require an object of primitive fields.")
    properties = schema.get("properties", {})
    if len(properties) > 32:
        raise ValueError("MCP forms support at most 32 fields.")

    def titled_options(value: Any) -> bool:
        return (
            isinstance(value, list)
            and bool(value)
            and all(
                isinstance(option, dict)
                and set(option) <= {"const", "title"}
                and isinstance(option.get("const"), str)
                and isinstance(option.get("title"), str)
                for option in value
            )
        )

    if not set(schema) <= {"type", "properties", "required", "title", "description", "additionalProperties"}:
        raise ValueError("Unsupported MCP form schema.")
    if not isinstance(schema.get("additionalProperties", False), bool):
        raise ValueError("Unsupported MCP form schema.")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(name, str) or name not in properties for name in required):
        raise ValueError("MCP forms can only require declared fields.")

    for prop in properties.values():
        if not isinstance(prop, dict) or prop.get("type") not in ("string", "number", "integer", "boolean", "array"):
            raise ValueError("MCP forms support only primitive fields and enum selections.")
        if prop.get("format") in ("password", "secret") or prop.get("writeOnly") is True:
            raise ValueError("Sensitive input must use an external browser flow, not an MCP form.")
        if not set(prop) <= {
            "type",
            "title",
            "description",
            "default",
            "format",
            "writeOnly",
            "minLength",
            "maxLength",
            "minimum",
            "maximum",
            "enum",
            "enumNames",
            "oneOf",
            "items",
            "minItems",
            "maxItems",
        }:
            raise ValueError("Unsupported MCP form field.")
        if "oneOf" in prop and (prop.get("type") != "string" or not titled_options(prop["oneOf"])):
            raise ValueError("MCP oneOf fields must be titled string selections.")
        if prop.get("type") == "array":
            items = prop.get("items", {})
            if not isinstance(items, dict) or not (
                (
                    set(items) == {"type", "enum"}
                    and items.get("type") == "string"
                    and isinstance(items["enum"], list)
                    and all(isinstance(option, str) for option in items["enum"])
                )
                or (set(items) == {"anyOf"} and titled_options(items["anyOf"]))
            ):
                raise ValueError("MCP arrays must be string enum selections.")

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("Invalid MCP form schema.") from exc
