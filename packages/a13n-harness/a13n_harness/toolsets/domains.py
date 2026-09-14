"""Explicit domain restrictions shared by the four Web tools."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _domain(value: str) -> str:
    if not value or value != value.strip() or any(char in value for char in "/:@?#\\"):
        raise ValueError("Domains must be hostnames, not URLs, ports, or credentials")
    if "*" in value:
        raise ValueError("Domain wildcards are not supported")
    host = value
    host = host.rstrip(".").encode("idna").decode("ascii").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP addresses are not domain names")
    if (
        not host
        or len(host) > 253
        or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(c.isascii() and (c.isalnum() or c == "-") for c in label)
            for label in host.split(".")
        )
    ):
        raise ValueError("Invalid domain name")
    return host


class DomainRestrictions(BaseModel):
    """Empty allow lists are unrestricted; bare hosts include their subdomains."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allow_domains: tuple[str, ...] = Field(default=(), max_length=256, exclude_if=lambda value: not value)
    deny_domains: tuple[str, ...] = Field(default=(), max_length=256, exclude_if=lambda value: not value)

    @field_validator("allow_domains", "deny_domains")
    @classmethod
    def _normalize(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(_domain(value) for value in values))

    @property
    def restricted(self) -> bool:
        return bool(self.allow_domains or self.deny_domains)

    def allows(self, url: str) -> bool:
        try:
            host = urlsplit(url).hostname
            if host is None:
                return False
            normalized = host.rstrip(".").encode("idna").decode("ascii").lower()
        except (ValueError, UnicodeError):
            return False

        def matches(domain: str) -> bool:
            return normalized == domain or normalized.endswith(f".{domain}")

        return not any(matches(pattern) for pattern in self.deny_domains) and (
            not self.allow_domains or any(matches(pattern) for pattern in self.allow_domains)
        )
