"""Exact operator allowlisting for provider-owned API origins."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit


def normalize_provider_origins(values: Iterable[str]) -> frozenset[str]:
    return frozenset(normalize_provider_origin(value) for value in values)


def require_provider_origin(
    value: str,
    *,
    official_origins: frozenset[str],
    allowed_custom_origins: frozenset[str],
) -> str:
    normalized = normalize_provider_origin(value)
    if normalized not in official_origins and normalized not in allowed_custom_origins:
        raise ValueError("provider API origin is not operator-allowed")
    return normalized


def normalize_provider_origin(value: str) -> str:
    if not value or len(value) > 2048:
        raise ValueError("provider API origin is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("provider API origin is invalid") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("provider API origin must be an exact HTTPS origin")
    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("provider API origin is invalid") from error
    if not hostname:
        raise ValueError("provider API origin is invalid")
    host = f"[{hostname}]" if ":" in hostname else hostname
    authority = host if port in {None, 443} else f"{host}:{port}"
    return urlunsplit(("https", authority, "", "", ""))
