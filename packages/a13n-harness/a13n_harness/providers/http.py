"""Bounded response mechanics shared by provider transports and hosts."""

from typing import Protocol

import httpx2


class EndpointValidator(Protocol):
    async def validate(self, endpoint: str, *, resolve_dns: bool = True) -> str: ...


class ProviderHttpError(Exception):
    def __init__(self, code: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


async def bounded_response_body(response: httpx2.Response, *, max_bytes: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            parsed = int(content_length)
        except ValueError as error:
            raise ProviderHttpError("invalid_provider_response") from error
        if parsed < 0:
            raise ProviderHttpError("invalid_provider_response")
        if parsed > max_bytes:
            raise ProviderHttpError("response_too_large")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ProviderHttpError("response_too_large")
    return bytes(body)


def retry_after_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if 0 <= parsed <= 3600 else None
