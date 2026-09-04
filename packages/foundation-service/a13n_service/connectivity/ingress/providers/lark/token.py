"""Async single-flight Lark tenant access token provider."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic

import anyio
import httpx2

from a13n_service.connectivity.http import EndpointValidator

from .api import LarkApiError, read_lark_response

_TOKEN_RESPONSE_MAX_BYTES = 64 * 1024


class LarkTenantTokenProvider:
    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        endpoint_validator: EndpointValidator,
        *,
        open_api_origin: str,
        app_id: str,
        app_secret: str,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not app_id or len(app_id) > 256 or not app_secret or len(app_secret) > 4096:
            raise ValueError("Lark application credentials are invalid")
        self._http_client = http_client
        self._endpoint_validator = endpoint_validator
        self._open_api_origin = open_api_origin.rstrip("/")
        self._app_id = app_id
        self._app_secret = app_secret
        self._clock = clock
        self._lock = anyio.Lock()
        self._token: str | None = None
        self._refresh_at = 0.0
        self._expires_at = 0.0

    async def token(self) -> str:
        if self._token is not None and self._clock() < self._refresh_at:
            return self._token
        async with self._lock:
            if self._token is not None and self._clock() < self._refresh_at:
                return self._token
            try:
                token, expires_in = await self._refresh()
            except LarkApiError:
                if self._token is not None and self._clock() < self._expires_at:
                    return self._token
                raise
            skew = min(60, max(1, expires_in // 10))
            self._token = token
            self._refresh_at = self._clock() + expires_in - skew
            self._expires_at = self._clock() + expires_in
            return token

    async def _refresh(self) -> tuple[str, int]:
        try:
            origin = await self._endpoint_validator.validate(self._open_api_origin, resolve_dns=True)
        except ValueError as error:
            raise LarkApiError("endpoint_denied") from error
        try:
            async with self._http_client.stream(
                "POST",
                f"{origin}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
                follow_redirects=False,
            ) as response:
                value = await read_lark_response(response, max_bytes=_TOKEN_RESPONSE_MAX_BYTES)
        except LarkApiError:
            raise
        except httpx2.HTTPError as error:
            raise LarkApiError("provider_unavailable") from error
        token = value.get("tenant_access_token")
        expires_in = value.get("expire")
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 4096
            or type(expires_in) is not int
            or expires_in <= 60
            or expires_in > 24 * 60 * 60
        ):
            raise LarkApiError("invalid_provider_response")
        return token, expires_in
