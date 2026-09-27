"""Canonical exact HTTP origins safe to place in CSP source lists."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit


def origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or any(char.isspace() or ord(char) < 32 for char in value)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Expected an HTTP(S) origin without credentials, path, query, or fragment.")
    port = parsed.port
    if ":" in parsed.hostname:
        host = f"[{ipaddress.IPv6Address(parsed.hostname)}]"
    else:
        host = parsed.hostname.encode("idna").decode("ascii")
        if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host):
            raise ValueError("Invalid origin hostname.")
    suffix = f":{port}" if port is not None and port != (443 if parsed.scheme == "https" else 80) else ""
    return f"{parsed.scheme}://{host}{suffix}"
