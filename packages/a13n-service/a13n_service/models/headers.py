"""Bounded, write-only Provider headers and their encrypted runtime values."""

from collections.abc import Iterable, Mapping
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, Field, SecretStr, StringConstraints

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


def _normalize_names(value: object) -> object:
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


def _validate_secret_values(value: dict[str, SecretStr | None]) -> dict[str, SecretStr | None]:
    for secret in value.values():
        if secret is not None:
            text = secret.get_secret_value()
            if len(text) > 2048 or any(not 32 <= ord(char) <= 126 for char in text):
                raise ValueError("header values must be bounded printable ASCII strings")
    return value


ExtraHeaders = Annotated[dict[HeaderName, HeaderValue], Field(max_length=32), BeforeValidator(_normalize_names)]
# Up to 32 removals plus 32 replacements; the resulting Provider still has at most 32 headers.
HeaderUpdates = Annotated[
    dict[HeaderName, SecretStr | None],
    Field(max_length=64, json_schema_extra={"writeOnly": True}),
    BeforeValidator(_normalize_names),
    AfterValidator(_validate_secret_values),
]


def validate_header_names(names: Iterable[str], *, reserved: Iterable[str]) -> None:
    names = set(names)
    if len(names) > 32:
        raise ValueError("a Provider accepts at most 32 extra headers")
    if names & set(reserved):
        raise ValueError("the header is managed by the Provider authentication or protocol")


def apply_header_updates[T](current: Mapping[str, T], updates: Mapping[str, T | None]) -> dict[str, T]:
    result = dict(current)
    for name, value in updates.items():
        if value is None:
            result.pop(name, None)
        else:
            result[name] = value
    return result
