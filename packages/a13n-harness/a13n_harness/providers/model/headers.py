"""Bounded, write-only Provider headers and their encrypted runtime values."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, Field, StringConstraints

HeaderValue = Annotated[str, StringConstraints(pattern=r"^[\x20-\x7e]*$", max_length=2048)]

_TRANSPORT_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "cookie",
        "host",
        "proxy-authorization",
        "set-cookie",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "user-agent",
    }
)


def _validate_name(name: str) -> str:
    name = name.lower()
    if name in _TRANSPORT_HEADERS:
        raise ValueError("the header is managed by the HTTP transport")
    return name


HeaderName = Annotated[
    str, StringConstraints(pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$"), AfterValidator(_validate_name)
]


def normalize_header_names(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    normalized: dict[str, object] = {}
    for name, item in value.items():
        if not isinstance(name, str):
            raise ValueError("header names must be strings")
        name = name.lower()
        if name in normalized:
            raise ValueError("header names must be unique ignoring case")
        normalized[name] = item
    return normalized


ExtraHeaders = Annotated[dict[HeaderName, HeaderValue], Field(max_length=32), BeforeValidator(normalize_header_names)]


def validate_header_names(names: Iterable[str], *, reserved: Iterable[str]) -> None:
    names = set(names)
    if len(names) > 32:
        raise ValueError("a Provider accepts at most 32 extra headers")
    if names & set(reserved):
        raise ValueError("the header is managed by the Provider authentication or protocol")
