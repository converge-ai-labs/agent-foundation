"""One normalized header contract for Connection auth and caller context; `check_header_name` owns the names."""

from collections.abc import Mapping

from a13n_service.providers.tools.mcp import MAX_HEADERS, check_header_name

MAX_VALUE_BYTES = 4096
MAX_HEADERS_BYTES = 8192


def normalize_headers(headers: Mapping[str, str], *, authentication: bool = False) -> dict[str, str]:
    if len(headers) > MAX_HEADERS:
        raise ValueError(f"At most {MAX_HEADERS} headers are allowed")
    normalized: dict[str, str] = {}
    size = 0
    for name, value in headers.items():
        folded = check_header_name(name.lower(), authentication=authentication)
        if folded in normalized:
            raise ValueError("Header names must be unique ignoring case")
        if len(value) > MAX_VALUE_BYTES:
            raise ValueError(f"Header values must contain at most {MAX_VALUE_BYTES} ASCII bytes")
        if any(ord(char) < 32 or ord(char) >= 127 for char in value):
            raise ValueError("Header values must contain only printable ASCII characters")
        size += len(name) + len(value)
        if size > MAX_HEADERS_BYTES:
            raise ValueError("Headers exceed their byte limit")
        normalized[folded] = value
    return normalized
