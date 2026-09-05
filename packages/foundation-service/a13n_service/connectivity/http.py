"""Shared bounded HTTP response mechanics for Connectivity adapters."""

from __future__ import annotations

from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from typing import Any, Protocol

import httpx2


class EndpointValidator(Protocol):
    async def validate(self, endpoint: str, *, resolve_dns: bool = True) -> str: ...


class ConnectivityHttpError(Exception):
    def __init__(self, code: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class BoundedHttpResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


async def cookie_free_bounded_request(
    client: httpx2.AsyncClient,
    method: str,
    endpoint: str,
    *,
    headers: dict[str, str],
    max_bytes: int | None,
    json_body: dict[str, Any] | None = None,
    content: bytes | None = None,
) -> BoundedHttpResponse:
    """Issue one cookie-free, non-following request without unbounded buffering."""

    request = client.build_request(
        method,
        endpoint,
        headers=headers,
        json=json_body,
        content=content,
    )
    if "cookie" in request.headers:
        del request.headers["cookie"]
    response = await client.send(request, stream=True, follow_redirects=False)
    try:
        response_headers = dict(response.headers)
        if response.status_code in {301, 302, 303, 307, 308} or max_bytes is None:
            body = b""
        else:
            body = await bounded_response_body(response, max_bytes=max_bytes)
        return BoundedHttpResponse(
            status_code=response.status_code,
            headers=response_headers,
            body=body,
        )
    finally:
        client.cookies.clear()
        await response.aclose()


async def bounded_response_body(response: httpx2.Response, *, max_bytes: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            parsed = int(content_length)
        except ValueError as error:
            raise ConnectivityHttpError("invalid_provider_response") from error
        if parsed < 0:
            raise ConnectivityHttpError("invalid_provider_response")
        if parsed > max_bytes:
            raise ConnectivityHttpError("response_too_large")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ConnectivityHttpError("response_too_large")
    return bytes(body)


def retry_after_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if 0 <= parsed <= 3600 else None


class _RejectCookies(DefaultCookiePolicy):
    def set_ok(self, cookie: Cookie, request: object) -> bool:
        return False

    def return_ok(self, cookie: Cookie, request: object) -> bool:
        return False


def cookie_free_jar() -> CookieJar:
    """Prevent a shared provider pool from retaining any account's cookies."""
    return CookieJar(policy=_RejectCookies())
