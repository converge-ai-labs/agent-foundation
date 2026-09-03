"""Validation and serialization for write-only MCP credential bundles."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import SecretStr

from a13n_service.connectivity.management import canonical_json

_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_DENIED_HEADERS = frozenset(
    {
        "accept",
        "authorization",
        "connection",
        "content-length",
        "content-type",
        "cookie",
        "forwarded",
        "host",
        "keep-alive",
        "last-event-id",
        "mcp-protocol-version",
        "mcp-session-id",
        "origin",
        "proxy-authenticate",
        "proxy-authorization",
        "referer",
        "set-cookie",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


class MCPCredentialError(ValueError):
    pass


def normalize_static_header_names(names: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted(_normalize_header_name(name) for name in names))
    if len(normalized) != len(set(normalized)):
        raise MCPCredentialError("static header names must be unique")
    if len(normalized) > 16:
        raise MCPCredentialError("too many static header names")
    return normalized


def bearer_bundle(value: SecretStr) -> str:
    bearer = _validate_value(value.get_secret_value())
    return canonical_json({"kind": "bearer", "bearer": bearer})


def static_header_bundle(
    values: Mapping[str, SecretStr],
    *,
    expected_names: tuple[str, ...],
) -> str:
    normalized = {
        _normalize_header_name(name): _validate_value(value.get_secret_value()) for name, value in values.items()
    }
    if tuple(sorted(normalized)) != expected_names:
        raise MCPCredentialError("static header values must match the configured names")
    return canonical_json({"kind": "static_headers", "headers": normalized})


def decode_request_headers(value: str) -> dict[str, str]:
    try:
        decoded: Any = json.loads(value)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise MCPCredentialError("credential bundle is invalid") from error
    if not isinstance(decoded, dict):
        raise MCPCredentialError("credential bundle is invalid")
    kind = decoded.get("kind")
    if kind == "bearer" and isinstance(decoded.get("bearer"), str):
        return {"Authorization": f"Bearer {_validate_value(decoded['bearer'])}"}
    if kind == "static_headers" and isinstance(decoded.get("headers"), dict):
        headers = decoded["headers"]
        if not all(isinstance(name, str) and isinstance(item, str) for name, item in headers.items()):
            raise MCPCredentialError("credential bundle is invalid")
        return {_normalize_header_name(name): _validate_value(item) for name, item in headers.items()}
    if kind == "oauth" and isinstance(decoded.get("access_token"), str):
        return {"Authorization": f"Bearer {_validate_value(decoded['access_token'])}"}
    raise MCPCredentialError("credential bundle is invalid")


def _normalize_header_name(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 128 or _HEADER_NAME.fullmatch(normalized) is None:
        raise MCPCredentialError("static header name is invalid")
    if normalized in _DENIED_HEADERS or normalized.startswith(("proxy-", "x-forwarded-")):
        raise MCPCredentialError("static header name is reserved")
    return normalized


def _validate_value(value: str) -> str:
    if not value or len(value.encode()) > 16_384 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise MCPCredentialError("credential value is invalid")
    return value
