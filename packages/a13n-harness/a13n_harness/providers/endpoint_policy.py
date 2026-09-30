"""Canonical outbound endpoint and redirect policy for Provider hosts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from urllib.parse import SplitResult, parse_qsl, urlsplit, urlunsplit

from a13n_harness.configuration import HostNotAllowedError, RunConfiguration, normalize_host

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


class EndpointPolicyError(ValueError):
    """A configurable endpoint violates the deployment outbound policy."""


@dataclass(frozen=True, slots=True)
class EndpointPolicy:
    """URL syntax, HTTPS and exact Run hostname authorization; no DNS or IP policy."""

    configuration: RunConfiguration = field(default_factory=RunConfiguration)
    require_https: bool = False
    allowed_http_origins: frozenset[str] = frozenset()

    @classmethod
    def from_http_origins(cls, *, require_https: bool = False, http_origins: Iterable[str] = ()) -> EndpointPolicy:
        return cls(
            require_https=require_https,
            allowed_http_origins=frozenset(_normalize_http_origin(value) for value in http_origins),
        )

    def for_run(self, configuration: RunConfiguration) -> EndpointPolicy:
        return replace(self, configuration=configuration)

    async def validate(self, endpoint: str) -> str:
        """Normalize and authorize the declared destination without resolving it."""
        return self.validate_syntax(endpoint)[0]

    async def validate_redirect(self, source: str, target: str) -> tuple[str, bool]:
        """Validate one redirect and report whether origin-bound credentials may be reused."""

        source_normalized, _, _ = self.validate_syntax(source)
        target_normalized = await self.validate(target)
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

        try:
            hostname = normalize_host(parsed.hostname)
            self.configuration.authorize_url(endpoint)
        except (HostNotAllowedError, ValueError) as error:
            raise EndpointPolicyError(str(error)) from error
        effective_port = port or (443 if parsed.scheme == "https" else 80)
        host = f"[{hostname}]" if ":" in hostname else hostname
        default_port = (parsed.scheme == "https" and effective_port == 443) or (
            parsed.scheme == "http" and effective_port == 80
        )
        authority = host if default_port else f"{host}:{effective_port}"
        path = parsed.path
        normalized = urlunsplit((parsed.scheme.lower(), authority, path, parsed.query, ""))
        if (
            self.require_https
            and parsed.scheme != "https"
            and _origin(urlsplit(normalized)) not in self.allowed_http_origins
        ):
            raise EndpointPolicyError("endpoint must use HTTPS")
        return normalized, hostname, effective_port


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
