"""Cookie-free HTTP operations for Service Connectivity clients."""

from __future__ import annotations

from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from typing import Any

import httpx2
from a13n_harness.http import bounded_response_body


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


class _RejectCookies(DefaultCookiePolicy):
    def set_ok(self, cookie: Cookie, request: object) -> bool:
        return False

    def return_ok(self, cookie: Cookie, request: object) -> bool:
        return False


def cookie_free_jar() -> CookieJar:
    """Prevent a shared provider pool from retaining any account's cookies."""
    return CookieJar(policy=_RejectCookies())
