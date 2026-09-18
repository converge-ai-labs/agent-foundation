"""Bounded, same-origin GitHub requests for account inspection and polling."""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx2
from a13n_harness.providers.http import EndpointValidator, ProviderHttpError
from pydantic import JsonValue

from .api import read_github_response
from .token import GITHUB_API_VERSION


@dataclass(frozen=True)
class GitHubResponse:
    data: JsonValue
    headers: httpx2.Headers
    status: int


class GitHubREST:
    def __init__(self, http: httpx2.AsyncClient, endpoints: EndpointValidator, api_origin: str) -> None:
        self.http = http
        self.endpoints = endpoints
        self.origin = api_origin.rstrip("/")

    async def request(
        self,
        path: str,
        *,
        token: str,
        method: Literal["GET", "PATCH"] = "GET",
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> GitHubResponse:
        url = self.origin + path if path.startswith("/") else path
        base, target = urlsplit(self.origin), urlsplit(url)
        if (
            (target.scheme, target.netloc) != (base.scheme, base.netloc)
            or target.username
            or target.password
            or target.fragment
        ):
            raise ProviderHttpError("endpoint_denied")
        # Enterprise API prefixes remain part of the credential boundary.
        if base.path and not target.path.startswith(base.path.rstrip("/") + "/"):
            raise ProviderHttpError("endpoint_denied")
        try:
            url = await self.endpoints.validate(url, resolve_dns=True)
        except ValueError as error:
            raise ProviderHttpError("endpoint_denied") from error
        try:
            async with self.http.stream(
                method,
                url,
                params=params,
                follow_redirects=False,
                headers={
                    "accept": "application/vnd.github+json",
                    "authorization": f"Bearer {token}",
                    "x-github-api-version": GITHUB_API_VERSION,
                    "user-agent": "a13n-service",
                    **(headers or {}),
                },
            ) as response:
                if response.status_code in {304, 404, 410}:
                    return GitHubResponse(None, response.headers, response.status_code)
                value = await read_github_response(response, max_bytes=2 * 1024 * 1024)
                return GitHubResponse(value, response.headers, response.status_code)
        except httpx2.HTTPError as error:
            raise ProviderHttpError("provider_unavailable") from error

    async def object(self, path: str, *, token: str) -> dict[str, JsonValue]:
        response = await self.request(path, token=token)
        if not isinstance(response.data, dict):
            raise ProviderHttpError(
                "provider_rejected" if response.status in {404, 410} else "invalid_provider_response"
            )
        return response.data


class GitHubPersonalTokenProvider:
    def __init__(self, rest: GitHubREST, value: str, user_id: int) -> None:
        self.rest, self.value, self.user_id = rest, value, user_id
        self.verified = False

    async def token(self, *, repository_id: int) -> str:
        if not self.verified:
            user = await self.rest.object("/user", token=self.value)
            if user.get("id") != self.user_id:
                raise ProviderHttpError("bot_identity_mismatch")
            self.verified = True
        return self.value
