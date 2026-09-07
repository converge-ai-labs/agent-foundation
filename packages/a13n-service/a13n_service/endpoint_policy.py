"""Canonical outbound endpoint and redirect policy for a13n Service."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import SplitResult, parse_qsl, urlsplit, urlunsplit

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
    """Normalize destinations and reject unsafe address or redirect changes."""

    allowed_private_domains: frozenset[str] = frozenset()
    allowed_private_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    require_https: bool = False
    allowed_http_origins: frozenset[str] = frozenset()

    @classmethod
    def from_operator_allowlist(
        cls,
        *,
        private_domains: Iterable[str] = (),
        private_cidrs: Iterable[str] = (),
        require_https: bool = False,
        http_origins: Iterable[str] = (),
    ) -> EndpointPolicy:
        domains = frozenset(_normalize_domain(value) for value in private_domains)
        networks = tuple(ipaddress.ip_network(value, strict=True) for value in private_cidrs)
        origins = frozenset(_normalize_http_origin(value) for value in http_origins)
        return cls(
            allowed_private_domains=domains,
            allowed_private_networks=networks,
            require_https=require_https,
            allowed_http_origins=origins,
        )

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

    async def validate_redirect(self, source: str, target: str, *, resolve_dns: bool = True) -> tuple[str, bool]:
        """Validate one redirect and report whether origin-bound credentials may be reused."""

        source_normalized, _, _ = self.validate_syntax(source)
        target_normalized = await self.validate(target, resolve_dns=resolve_dns)
        source_parsed = urlsplit(source_normalized)
        target_parsed = urlsplit(target_normalized)
        source_origin = _origin(source_parsed)
        target_origin = _origin(target_parsed)
        if source_parsed.scheme == "https" and target_parsed.scheme == "http":
            raise EndpointPolicyError("HTTPS endpoints cannot redirect to HTTP")
        return target_normalized, source_origin == target_origin

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
        if (
            self.require_https
            and parsed.scheme != "https"
            and _origin(urlsplit(normalized)) not in self.allowed_http_origins
        ):
            raise EndpointPolicyError("endpoint must use HTTPS")
        return normalized, hostname, effective_port

    def validate_address(self, hostname: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        if address in _CLOUD_METADATA_ADDRESSES:
            raise EndpointPolicyError("cloud metadata destinations are denied")
        if (
            address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified
        ) and not self._allows_private(hostname, address):
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


def _normalize_http_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise EndpointPolicyError("HTTP exception must be an exact origin")
    policy = EndpointPolicy()
    normalized, _, _ = policy.validate_syntax(value)
    return _origin(urlsplit(normalized))


def _origin(parsed: SplitResult) -> str:
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if hostname is None:
        raise EndpointPolicyError("endpoint must contain a hostname")
    port = parsed.port or (443 if scheme == "https" else 80)
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    return f"{scheme}://{host}" if default_port else f"{scheme}://{host}:{port}"


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
