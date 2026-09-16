"""Withhold known credential-bearing configuration fields from model context."""

from collections.abc import Mapping
from urllib.parse import parse_qsl, urlsplit

from pydantic import JsonValue

PROTECTED = "[protected configuration value; preserve with a bounded patch]"
_CREDENTIAL_FIELDS = frozenset(
    {
        "apikey",
        "authorization",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "password",
        "clientsecret",
        "credential",
        "credentials",
        "secretvalue",
        "cookie",
        "cookies",
        "headers",
        "extraheaders",
    }
)


def protected_field(name: str) -> bool:
    return name.lower().replace("_", "").replace("-", "") in _CREDENTIAL_FIELDS


def credential_url(value: str) -> bool:
    if not value.startswith(("https://", "http://")):
        return False
    try:
        url = urlsplit(value)
        return bool(url.username or url.password or any(protected_field(key) for key, _ in parse_qsl(url.query)))
    except ValueError:
        return True


def model_safe(value: JsonValue, *, depth: int = 0) -> JsonValue:
    """This is a known-field ceiling, not a promise to detect secrets in prose."""
    if depth > 32:
        return PROTECTED
    if isinstance(value, dict):
        result = {
            key: PROTECTED if protected_field(key) else model_safe(item, depth=depth + 1) for key, item in value.items()
        }
        # Diff values may omit the field name, so use their complete authoring path.
        path = value.get("path")
        if isinstance(path, list) and any(isinstance(part, str) and protected_field(part) for part in path):
            for key in ("before", "after"):
                if key in result:
                    result[key] = PROTECTED
        return result
    if isinstance(value, list):
        return [model_safe(item, depth=depth + 1) for item in value]
    if isinstance(value, str) and credential_url(value):
        return PROTECTED
    return value


def contains_protected_input(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(protected_field(str(key)) or contains_protected_input(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_protected_input(item) for item in value)
    return isinstance(value, str) and (PROTECTED in value or credential_url(value))
