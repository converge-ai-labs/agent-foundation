"""Bounded async Native transport. Mutations are never replayed automatically."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx2
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from .models import (
    CreateSearchProviderRequest,
    Page,
    Representation,
    SearchProvider,
    SearchProviderDefinition,
    SearchProviderReference,
    SearchProviderTestResult,
    UpdateSearchProviderRequest,
)


class ProtocolError(Exception):
    """A malformed or oversized Service response."""


class TransportError(Exception):
    """Transport failed; a mutation's outcome can be unknown."""


@dataclass(repr=False)
class ApiError(Exception):
    status: int
    code: str
    message: str
    details: dict[str, JsonValue] = field(default_factory=dict)
    request_id: str | None = None
    retry_after: str | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message} ({self.status})"

    def __repr__(self) -> str:
        return f"ApiError(status={self.status}, code={self.code!r})"


@dataclass(frozen=True)
class SearchScope:
    kind: Literal["workspace", "organization"]
    id: str

    @property
    def path(self) -> str:
        if self.kind not in {"workspace", "organization"}:
            raise ValueError("Invalid search scope")
        return f"/{self.kind}s/{_segment(self.id)}/search-providers"


def _segment(value: str) -> str:
    if not value or value in {".", ".."}:
        raise ValueError("A nonblank resource identifier is required")
    return quote(value, safe="")


class _CredentialContext(BaseModel):
    workspace_id: str | None = None


class _EmptyRequest(BaseModel):
    pass


class Client:
    """Bearer client for the Search Provider surface of Native /api/v1.

    Owns its transport, including a caller-provided transport. Use as an async
    context manager or call aclose(). Every operation makes one HTTP request.
    """

    def __init__(
        self, base_url: str, token: str, *, timeout: float = 30, transport: httpx2.AsyncBaseTransport | None = None
    ):
        url = urlsplit(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("base_url must be an HTTP(S) URL without credentials, query, or fragment")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._timeout = timeout
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self._http = httpx2.AsyncClient(
            base_url=base_url.rstrip("/") + "/api/v1/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.aclose()

    async def aclose(self) -> None:
        self._closed = True
        current = asyncio.current_task()
        active = tuple(task for task in self._tasks if task is not current)
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        self._http.headers.clear()
        await self._http.aclose()

    async def _request[T](
        self,
        method: str,
        path: str,
        result_type: type[T],
        *,
        body: BaseModel | None = None,
        etag: str | None = None,
        params: dict | None = None,
    ) -> Representation[T]:
        if self._closed:
            raise TransportError("Client is closed")
        payload = body.model_dump(mode="json", exclude_unset=True) if body else None
        if isinstance(body, CreateSearchProviderRequest | UpdateSearchProviderRequest) and body.credential is not None:
            assert payload is not None
            payload["credential"] = body.credential.get_secret_value()
        task = asyncio.current_task()
        if task:
            self._tasks.add(task)
        try:
            async with asyncio.timeout(self._timeout):
                async with self._http.stream(
                    method, path.lstrip("/"), json=payload, headers={"If-Match": etag} if etag else None, params=params
                ) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1_048_576:
                            raise ProtocolError("Service response exceeded the byte limit")
                    try:
                        value = json.loads(raw)
                    except (ValueError, UnicodeError):
                        raise ProtocolError("Service returned invalid JSON") from None
                    request_id = response.headers.get("X-Request-ID")
                    if not response.is_success:
                        error = value.get("error", {}) if isinstance(value, dict) else {}
                        if not isinstance(error, dict):
                            error = {}
                        raise ApiError(
                            response.status_code,
                            code if isinstance(code := error.get("code"), str) else "http_error",
                            message if isinstance(message := error.get("message"), str) else "Service request failed",
                            details if isinstance(details := error.get("details"), dict) else {},
                            error.get("request_id") if isinstance(error.get("request_id"), str) else request_id,
                            response.headers.get("Retry-After"),
                        )
                    try:
                        parsed = TypeAdapter(result_type).validate_python(value)
                    except ValidationError:
                        raise ProtocolError("Service returned an invalid representation") from None
                    return Representation(value=parsed, etag=response.headers.get("ETag"), request_id=request_id)
        except (httpx2.HTTPError, TimeoutError):
            raise TransportError("Service transport failed; mutation outcome may be unknown") from None
        finally:
            if payload is not None:
                payload.clear()
            if task:
                self._tasks.discard(task)

    async def workspace(self) -> "WorkspaceClient":
        """Bind operations to the API key's Workspace, sharing this transport."""
        context = (await self._request("GET", "/auth/context", _CredentialContext)).value
        if not context.workspace_id:
            raise ValueError("Workspace operations require a Workspace-bound credential")
        return WorkspaceClient(self, context.workspace_id)

    async def search_provider_types(self) -> Page[SearchProviderDefinition]:
        return (await self._request("GET", "/search-provider-types", Page[SearchProviderDefinition])).value

    async def search_provider_type(self, provider_type: str) -> SearchProviderDefinition:
        return (
            await self._request("GET", f"/search-provider-types/{_segment(provider_type)}", SearchProviderDefinition)
        ).value

    async def search_providers(
        self,
        scope: SearchScope,
        *,
        cursor: str | None = None,
        limit: int = 100,
        type: str | None = None,
        enabled: bool | None = None,
    ) -> Page[SearchProvider]:
        params = {
            key: value
            for key, value in {"cursor": cursor, "limit": limit, "type": type, "enabled": enabled}.items()
            if value is not None
        }
        return (await self._request("GET", scope.path, Page[SearchProvider], params=params)).value

    async def search_provider(self, scope: SearchScope, provider_id: str) -> Representation[SearchProvider]:
        return await self._request("GET", f"{scope.path}/{_segment(provider_id)}", SearchProvider)

    async def create_search_provider(
        self, scope: SearchScope, request: CreateSearchProviderRequest
    ) -> Representation[SearchProvider]:
        return await self._request("POST", scope.path, SearchProvider, body=request)

    async def update_search_provider(
        self, scope: SearchScope, provider_id: str, etag: str, request: UpdateSearchProviderRequest
    ) -> Representation[SearchProvider]:
        if not etag or etag.startswith("W/"):
            raise ValueError("A strong account ETag is required")
        return await self._request(
            "PATCH", f"{scope.path}/{_segment(provider_id)}", SearchProvider, body=request, etag=etag
        )

    async def test_search_provider(self, scope: SearchScope, provider_id: str) -> SearchProviderTestResult:
        # Explicit empty object; a saved-account probe is never retried.
        return (
            await self._request(
                "POST", f"{scope.path}/{_segment(provider_id)}/test", SearchProviderTestResult, body=_EmptyRequest()
            )
        ).value

    async def search_provider_references(
        self, scope: SearchScope, provider_id: str, *, cursor: str | None = None, limit: int = 100
    ) -> Page[SearchProviderReference]:
        params = {"limit": limit, **({"cursor": cursor} if cursor is not None else {})}
        return (
            await self._request(
                "GET", f"{scope.path}/{_segment(provider_id)}/references", Page[SearchProviderReference], params=params
            )
        ).value


class WorkspaceClient:
    """Search operations bound to an immutable Workspace ID by Client.workspace()."""

    def __init__(self, client: Client, workspace_id: str):
        self._client = client
        self._scope = SearchScope("workspace", workspace_id)

    async def search_providers(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
        type: str | None = None,
        enabled: bool | None = None,
    ) -> Page[SearchProvider]:
        return await self._client.search_providers(self._scope, cursor=cursor, limit=limit, type=type, enabled=enabled)

    async def search_provider(self, provider_id: str) -> Representation[SearchProvider]:
        return await self._client.search_provider(self._scope, provider_id)

    async def create_search_provider(self, request: CreateSearchProviderRequest) -> Representation[SearchProvider]:
        return await self._client.create_search_provider(self._scope, request)

    async def update_search_provider(
        self,
        provider_id: str,
        etag: str,
        request: UpdateSearchProviderRequest,
    ) -> Representation[SearchProvider]:
        return await self._client.update_search_provider(self._scope, provider_id, etag, request)

    async def test_search_provider(self, provider_id: str) -> SearchProviderTestResult:
        return await self._client.test_search_provider(self._scope, provider_id)

    async def search_provider_references(
        self,
        provider_id: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> Page[SearchProviderReference]:
        return await self._client.search_provider_references(self._scope, provider_id, cursor=cursor, limit=limit)
