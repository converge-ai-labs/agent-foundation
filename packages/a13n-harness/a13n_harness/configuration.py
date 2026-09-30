"""Immutable caller-owned configuration shared by the consumers of one Run."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_serializer, field_validator


@dataclass(frozen=True, slots=True)
class _ExtensionValues(Mapping[str, JsonValue]):
    """Immutable storage; each lookup returns a detached JSON value."""

    _values: Mapping[str, str]

    def __getitem__(self, key: str) -> JsonValue:
        return json.loads(self._values[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __deepcopy__(self, memo: dict[int, object]) -> _ExtensionValues:
        return self


class HostNotAllowedError(ValueError):
    """A URL's declared hostname is outside the accepted Run's allowed hosts."""


class RunConfiguration(BaseModel):
    """An accepted snapshot, independent of Agent definitions and Capability configuration.

    Consumers explicitly opt into namespaced extensions and own their validation.
    Extension lookups return detached values, not mutable shared state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    allowed_hosts: frozenset[str] | None = None
    extensions: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _host_array(cls, value: object) -> object:
        # Hosts are JSON arrays on the wire, including under Host before-validators in strict mode.
        if isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                raise ValueError("allowed hosts must contain strings")
            return frozenset(value)
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def _normalize_hosts(cls, value: frozenset[str] | None) -> frozenset[str] | None:
        return None if value is None else frozenset(normalize_host(host) for host in value)

    @field_validator("extensions")
    @classmethod
    def _snapshot_extensions(cls, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        return _ExtensionValues(
            MappingProxyType({key: json.dumps(item, sort_keys=True, allow_nan=False) for key, item in value.items()})
        )

    @field_serializer("allowed_hosts")
    def _serialize_hosts(self, value: frozenset[str] | None) -> list[str] | None:
        return None if value is None else sorted(value)

    @field_serializer("extensions")
    def _serialize_extensions(self, value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        return dict(value)

    def authorize_url(self, url: str) -> None:
        """Check a declared HTTP(S) hostname without resolving DNS or inspecting routing."""
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
            # Accessing port validates bracket/port syntax even though ports are not an authority axis.
            if parsed.port == 0:
                raise ValueError("URL port cannot be zero")
        except ValueError as error:
            raise HostNotAllowedError("URL is not valid") from error
        if parsed.scheme not in {"http", "https"} or hostname is None:
            raise HostNotAllowedError("URL must have an HTTP(S) hostname")
        try:
            host = normalize_host(hostname)
        except ValueError as error:
            raise HostNotAllowedError("URL has an invalid hostname") from error
        if self.allowed_hosts is not None and host not in self.allowed_hosts:
            raise HostNotAllowedError(f"URL hostname {host!r} is not allowed for this Run")


def normalize_host(value: str) -> str:
    """Canonical exact DNS hostname or IP literal; never a URL, wildcard, CIDR or port."""
    host = value.strip().rstrip(".").lower()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("invalid hostname") from error
    if len(host) > 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")
    ):
        raise ValueError("allowed hosts must be exact DNS hostnames or IP literals")
    return host
