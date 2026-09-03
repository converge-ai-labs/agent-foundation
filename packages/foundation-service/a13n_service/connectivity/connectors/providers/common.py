"""Small validation helpers shared by built-in Connector adapters."""

from __future__ import annotations

from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import BaseModel, JsonValue, TypeAdapter

from a13n_service.connectivity.ingress.domain import JsonObject
from a13n_service.endpoint_policy import EndpointPolicy

_JSON_OBJECT = TypeAdapter(JsonObject)


def model_json(value: BaseModel) -> JsonObject:
    return _JSON_OBJECT.validate_python(value.model_dump(mode="json"))


def normalized_endpoint(value: str) -> str:
    normalized, _, _ = EndpointPolicy(require_https=True).validate_syntax(value)
    parsed = urlsplit(normalized)
    if parsed.query or parsed.path:
        raise ValueError("Connector endpoint must be an HTTPS origin")
    return normalized


def same_origin_url(value: JsonValue | None, *, endpoint: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        raise ValueError("Connector redirect URL is invalid")
    parsed = urlsplit(value)
    expected = urlsplit(endpoint)
    if (
        parsed.scheme != expected.scheme
        or parsed.hostname != expected.hostname
        or (parsed.port or 443) != (expected.port or 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("Connector redirect URL has an unexpected origin")
    return urlunsplit(parsed)


def path_segment(value: str) -> str:
    if not value or len(value) > 2048:
        raise ValueError("Connector external reference is invalid")
    return quote(value, safe="")


def required_string(value: JsonObject, key: str, *, max_length: int = 2048) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not 1 <= len(selected) <= max_length:
        raise ValueError(f"Connector response has invalid {key}")
    return selected


def optional_string(value: JsonValue | None, *, max_length: int = 2048) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length:
        raise ValueError("Connector response has an invalid string")
    return value


def required_object(value: JsonValue | None) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("Connector response object is invalid")
    return value
