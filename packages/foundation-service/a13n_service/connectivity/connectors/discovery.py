"""Validate safe transient Connector directories at the application boundary."""

from a13n_service.connectivity.bounds import DISCOVERY_MAX_BYTES, DISCOVERY_MAX_TOOLS, TOOL_SCHEMA_MAX_BYTES
from a13n_service.connectivity.management import canonical_json
from a13n_service.connectivity.tool_validation import require_depth, validate_schema

from .contracts import ConnectorProviderError, DiscoveredConnector

_CREDENTIAL_FIELDS = frozenset(
    {
        "credential",
        "credentials",
        "password",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "cookie",
        "client_secret",
    }
)


def validate_connectors(items: tuple[DiscoveredConnector, ...]) -> None:
    if len(items) > DISCOVERY_MAX_TOOLS:
        raise ConnectorProviderError("directory_too_large")
    seen: set[str] = set()
    size = 0
    for item in items:
        if item.key in seen:
            raise ConnectorProviderError("invalid_provider_response")
        seen.add(item.key)
        require_depth(item.setup_schema)
        if len(canonical_json(item.setup_schema).encode()) > TOOL_SCHEMA_MAX_BYTES:
            raise ConnectorProviderError("directory_schema_too_large")
        pending: list[object] = [item.setup_schema]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if value.get("writeOnly") is True or value.get("format") == "password":
                    raise ConnectorProviderError("unsafe_setup_schema")
                if "$ref" in value or "$dynamicRef" in value:
                    raise ConnectorProviderError("unsafe_setup_schema")
                properties = value.get("properties")
                if isinstance(properties, dict) and any(
                    str(key).casefold() in _CREDENTIAL_FIELDS for key in properties
                ):
                    raise ConnectorProviderError("unsafe_setup_schema")
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        validate_schema(item.setup_schema, require_object=False)
        size += len(item.model_dump_json().encode())
        if size > DISCOVERY_MAX_BYTES:
            raise ConnectorProviderError("directory_too_large")
