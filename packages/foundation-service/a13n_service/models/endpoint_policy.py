"""Outbound destination validation for configurable model endpoints."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from anyio import to_thread

_SENSITIVE_QUERY_NAMES = frozenset(
    {
        "access_token",
        "api-key",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "key",
        "password",
        "secret",
        "signature",
        "token",
    }
)
_CLOUD_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)


class EndpointPolicyError(ValueError):
    """A configurable endpoint violates the deployment outbound policy."""


@dataclass(frozen=True, slots=True)
class EndpointPolicy:
    allowed_private_domains: frozenset[str] = frozenset()
    allowed_private_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()

    @classmethod
    def from_operator_allowlist(
        cls,
        *,
        private_domains: Iterable[str] = (),
        private_cidrs: Iterable[str] = (),
    ) -> EndpointPolicy:
        domains = frozenset(_normalize_domain(value) for value in private_domains)
        networks = tuple(ipaddress.ip_network(value, strict=True) for value in private_cidrs)
        return cls(allowed_private_domains=domains, allowed_private_networks=networks)

    async def validate(self, endpoint: str, *, resolve_dns: bool = True) -> str:
        """Normalize and validate an endpoint, including its current DNS answers."""

        normalized, hostname, port = self.validate_syntax(endpoint)
        try:
            literal_address = ipaddress.ip_address(hostname)
        except ValueError:
            literal_address = None
        if literal_address is not None:
            self.validate_address(hostname, literal_address)
            return normalized
        if resolve_dns:
            addresses = await to_thread.run_sync(_resolve_addresses, hostname, port)
            if not addresses:
                raise EndpointPolicyError("endpoint hostname has no address")
            for address in addresses:
                self.validate_address(hostname, address)
        return normalized

    def validate_syntax(self, endpoint: str) -> tuple[str, str, int]:
        try:
            parsed = urlsplit(endpoint)
            port = parsed.port
        except ValueError as error:
            raise EndpointPolicyError("endpoint is not a valid URL") from error
        if parsed.scheme not in {"http", "https"}:
            raise EndpointPolicyError("endpoint scheme must be http or https")
        if parsed.username is not None or parsed.password is not None:
            raise EndpointPolicyError("endpoint must not contain user information")
        if parsed.fragment:
            raise EndpointPolicyError("endpoint must not contain a fragment")
        if not parsed.hostname:
            raise EndpointPolicyError("endpoint must contain a hostname")
        if any(name.lower() in _SENSITIVE_QUERY_NAMES for name, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            raise EndpointPolicyError("endpoint query contains a sensitive parameter")

        hostname = _normalize_domain(parsed.hostname)
        effective_port = port or (443 if parsed.scheme == "https" else 80)
        host = f"[{hostname}]" if ":" in hostname else hostname
        default_port = (parsed.scheme == "https" and effective_port == 443) or (
            parsed.scheme == "http" and effective_port == 80
        )
        authority = host if default_port else f"{host}:{effective_port}"
        path = parsed.path.rstrip("/") or ""
        normalized = urlunsplit((parsed.scheme.lower(), authority, path, parsed.query, ""))
        return normalized, hostname, effective_port

    def validate_address(self, hostname: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        if address in _CLOUD_METADATA_ADDRESSES:
            raise EndpointPolicyError("cloud metadata destinations are denied")
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
            raise EndpointPolicyError("non-routable endpoint destination is denied")
        if address.is_private and not self._allows_private(hostname, address):
            raise EndpointPolicyError("private endpoint destination is not allowlisted")

    def _allows_private(self, hostname: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        domain_allowed = any(
            hostname == domain or hostname.endswith(f".{domain}") for domain in self.allowed_private_domains
        )
        network_allowed = any(address in network for network in self.allowed_private_networks)
        return domain_allowed or network_allowed


def _normalize_domain(value: str) -> str:
    domain = value.strip().rstrip(".").lower()
    if not domain or len(domain) > 253:
        raise EndpointPolicyError("invalid domain name")
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise EndpointPolicyError("invalid domain name") from error


def _resolve_addresses(hostname: str, port: int) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            results = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as error:
            raise EndpointPolicyError("endpoint hostname could not be resolved") from error
        return tuple(dict.fromkeys(ipaddress.ip_address(result[4][0]) for result in results))
    return (literal,)
