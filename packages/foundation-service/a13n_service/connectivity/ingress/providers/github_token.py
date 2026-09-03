"""Async single-flight GitHub App installation token provider."""

from __future__ import annotations

import base64
import json
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio
import httpx2
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from a13n_service.connectivity.http import EndpointValidator
from a13n_service.connectivity.ingress.domain import JsonObject

from .github_api import GitHubApiError, read_github_response

GITHUB_API_VERSION = "2026-03-10"
_TOKEN_RESPONSE_MAX_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class _CachedToken:
    value: str
    refresh_at: datetime
    expires_at: datetime


class GitHubInstallationTokenProvider:
    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        endpoint_validator: EndpointValidator,
        *,
        api_origin: str,
        app_id: int,
        installation_id: int,
        private_key_pem: str,
        permissions: JsonObject,
        max_cached_repositories: int = 512,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if app_id <= 0 or installation_id <= 0 or not 1 <= len(private_key_pem) <= 32 * 1024:
            raise ValueError("GitHub App credentials are invalid")
        if not permissions or any(
            key not in {"issues", "pull_requests"} or not isinstance(value, str) or value not in {"read", "write"}
            for key, value in permissions.items()
        ):
            raise ValueError("GitHub installation permissions are invalid")
        if not 1 <= max_cached_repositories <= 4096:
            raise ValueError("GitHub token cache bound is invalid")
        private_key = load_github_private_key(private_key_pem)
        self._http_client = http_client
        self._endpoint_validator = endpoint_validator
        self._api_origin = api_origin.rstrip("/")
        self._app_id = app_id
        self._installation_id = installation_id
        self._private_key = private_key
        self._permissions = dict(permissions)
        self._clock = clock
        self._lock = anyio.Lock()
        self._max_cached_repositories = max_cached_repositories
        self._tokens: OrderedDict[int, _CachedToken] = OrderedDict()

    async def token(self, *, repository_id: int) -> str:
        if repository_id <= 0:
            raise GitHubApiError("invalid_binding")
        now = self._clock()
        cached = self._tokens.get(repository_id)
        if cached is not None and now < cached.refresh_at:
            self._tokens.move_to_end(repository_id)
            return cached.value
        async with self._lock:
            now = self._clock()
            cached = self._tokens.get(repository_id)
            if cached is not None and now < cached.refresh_at:
                self._tokens.move_to_end(repository_id)
                return cached.value
            try:
                refreshed = await self._refresh(repository_id=repository_id, now=now)
            except GitHubApiError:
                if cached is not None and now < cached.expires_at:
                    return cached.value
                raise
            self._tokens[repository_id] = refreshed
            self._tokens.move_to_end(repository_id)
            while len(self._tokens) > self._max_cached_repositories:
                self._tokens.popitem(last=False)
            return refreshed.value

    async def _refresh(self, *, repository_id: int, now: datetime) -> _CachedToken:
        try:
            origin = await self._endpoint_validator.validate(self._api_origin, resolve_dns=True)
        except ValueError as error:
            raise GitHubApiError("endpoint_denied") from error
        jwt = self._app_jwt(now)
        try:
            async with self._http_client.stream(
                "POST",
                f"{origin}/app/installations/{self._installation_id}/access_tokens",
                headers={
                    "accept": "application/vnd.github+json",
                    "authorization": f"Bearer {jwt}",
                    "x-github-api-version": GITHUB_API_VERSION,
                },
                json={"repository_ids": [repository_id], "permissions": self._permissions},
                follow_redirects=False,
            ) as response:
                value = await read_github_response(response, max_bytes=_TOKEN_RESPONSE_MAX_BYTES)
        except GitHubApiError:
            raise
        except httpx2.HTTPError as error:
            raise GitHubApiError("provider_unavailable") from error
        if not isinstance(value, dict):
            raise GitHubApiError("invalid_provider_response")
        token = value.get("token")
        expires_at_value = value.get("expires_at")
        if not isinstance(token, str) or not 1 <= len(token) <= 4096 or not isinstance(expires_at_value, str):
            raise GitHubApiError("invalid_provider_response")
        try:
            expires_at = datetime.fromisoformat(expires_at_value.replace("Z", "+00:00"))
        except ValueError as error:
            raise GitHubApiError("invalid_provider_response") from error
        if expires_at.tzinfo is None or expires_at <= now + timedelta(minutes=2):
            raise GitHubApiError("invalid_provider_response")
        return _CachedToken(
            value=token,
            refresh_at=expires_at - timedelta(seconds=60),
            expires_at=expires_at,
        )

    def _app_jwt(self, now: datetime) -> str:
        if now.tzinfo is None:
            raise GitHubApiError("invalid_clock")
        header = _base64url_json({"alg": "RS256", "typ": "JWT"})
        payload = _base64url_json(
            {
                "iat": int((now - timedelta(seconds=60)).timestamp()),
                "exp": int((now + timedelta(minutes=9)).timestamp()),
                "iss": str(self._app_id),
            }
        )
        signing_input = f"{header}.{payload}".encode()
        signature = self._private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{payload}.{_base64url(signature)}"


def _base64url_json(value: JsonObject) -> str:
    return _base64url(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def load_github_private_key(value: str) -> rsa.RSAPrivateKey:
    try:
        private_key = serialization.load_pem_private_key(value.encode(), password=None)
    except (TypeError, ValueError) as error:
        raise ValueError("GitHub App private key is invalid") from error
    if not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size < 2048:
        raise ValueError("GitHub App private key must be RSA with at least 2048 bits")
    return private_key
