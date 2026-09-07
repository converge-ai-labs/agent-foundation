"""Bounded OAuth transport over the process-owned HTTP pool."""

from __future__ import annotations

import json

import httpx2

from a13n_service.connectivity.http import ConnectivityHttpError, cookie_free_bounded_request
from a13n_service.endpoint_policy import EndpointPolicy


class OAuthTransport(httpx2.AsyncBaseTransport):
    """Give Authlib a cookie-free request boundary without owning another pool."""

    def __init__(self, client: httpx2.AsyncClient, policy: EndpointPolicy, *, max_bytes: int) -> None:
        self._client = client
        self._policy = policy
        self._max_bytes = max_bytes

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        endpoint = await self._policy.validate(str(request.url), resolve_dns=True)
        headers = dict(request.headers)
        headers.pop("cookie", None)
        response = await cookie_free_bounded_request(
            self._client,
            request.method,
            endpoint,
            headers=headers,
            max_bytes=self._max_bytes,
            content=await request.aread(),
        )
        if 300 <= response.status_code < 400:
            raise ConnectivityHttpError("oauth_redirect_forbidden")
        if response.status_code >= 500:
            raise ConnectivityHttpError("token_exchange_unavailable")
        if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ConnectivityHttpError("invalid_oauth_response")
        try:
            value = json.loads(response.body)
        except (ValueError, RecursionError) as error:
            raise ConnectivityHttpError("invalid_oauth_response") from error
        if not isinstance(value, dict) or ("error" in value and not isinstance(value["error"], str)):
            raise ConnectivityHttpError("invalid_oauth_response")
        # Authlib parses standard OAuth errors but does not reject every non-200
        # response itself. A provider error cannot be accepted as a token bundle.
        if response.status_code != 200 and not isinstance(value.get("error"), str):
            raise ConnectivityHttpError("token_exchange_failed")
        # The bounded reader already decoded content encoding. Do not decode it
        # again or retain response cookies in Authlib's per-operation client.
        return httpx2.Response(
            response.status_code,
            headers={"content-type": "application/json"},
            content=response.body,
        )
