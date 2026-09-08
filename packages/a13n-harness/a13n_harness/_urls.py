"""Shared validation and model-safe projection for HTTP(S) URLs."""

from __future__ import annotations

import re
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

from a13n_harness._json import is_sensitive_key

_MAX_QUERY_FIELDS = 128
_CREDENTIAL_QUERY_ALIASES = frozenset(
    {
        "accesskey",
        "accesskeyid",
        "apikey",
        "auth",
        "authorizationcode",
        "clientsecret",
        "code",
        "jwt",
        "key",
        "keypairid",
        "oauth",
        "oauthcode",
        "oauthverifier",
        "session",
        "sessionid",
        "sig",
        "subscriptionkey",
    }
)
_QUERY_KEY_SEPARATORS = re.compile(r"[^a-z0-9]+")


def require_http_url(value: str) -> SplitResult:
    """Validate one transport URL while allowing an exact credential-bearing query."""
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("URL must be a string without NUL")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("URL must be HTTP(S) without userinfo")
    _ = parsed.port
    return parsed


def is_credential_query_key(key: str) -> bool:
    """Recognize common credential query names, including separator-free aliases."""
    compact = _QUERY_KEY_SEPARATORS.sub("", key.casefold())
    return (
        is_sensitive_key(key)
        or compact in _CREDENTIAL_QUERY_ALIASES
        or "signature" in compact
        or compact.endswith("secretkey")
    )


def require_audience_safe_url(value: str) -> None:
    """Reject authority-bearing components from a URL that may enter model context."""
    parsed = require_http_url(value)
    if parsed.fragment:
        raise ValueError("model-visible URLs must not contain fragments")
    query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=_MAX_QUERY_FIELDS)
    if any(is_credential_query_key(key) for key, _ in query):
        raise ValueError("model-visible URLs must not contain credential-bearing query parameters")


def project_audience_safe_url(value: str) -> str:
    """Strip known credential pairs and fragments from an exact HTTP(S) URL."""
    try:
        parsed = require_http_url(value)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        query = urlencode(
            [
                (key, item)
                for key, item in parse_qsl(
                    parsed.query,
                    keep_blank_values=True,
                    max_num_fields=_MAX_QUERY_FIELDS,
                )
                if not is_credential_query_key(key)
            ],
            doseq=True,
        )
        return urlunsplit((parsed.scheme, host, parsed.path, query, ""))
    except (TypeError, ValueError):
        return "<invalid-url>"
