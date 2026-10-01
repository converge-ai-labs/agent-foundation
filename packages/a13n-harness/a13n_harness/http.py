"""Bounded HTTP response mechanics shared by Harness transports and their hosts."""

import os
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx2

# Connector and host transports never honour a delay longer than this.
MAX_RETRY_AFTER_SECONDS = 3600.0
OUTBOUND_TLS_VERIFY_ENV = "A13N_OUTBOUND_TLS_VERIFY"


def outbound_tls_verify() -> bool:
    """Read the operator-only TLS setting when constructing an owned HTTP client.

    Unset means verified TLS. Only explicit ``false`` disables certificate chain
    and hostname verification; malformed values never silently weaken TLS.
    Caller-supplied clients, transports and CA contexts keep their own policy.
    """
    value = os.environ.get(OUTBOUND_TLS_VERIFY_ENV)
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{OUTBOUND_TLS_VERIFY_ENV} must be true or false")
    return normalized == "true"


class EndpointValidator(Protocol):
    async def validate(self, endpoint: str) -> str: ...


class ProviderHttpError(Exception):
    def __init__(self, code: str, *, retry_after_seconds: float | None = None) -> None:
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
        if len(body) + len(chunk) > max_bytes:
            raise ProviderHttpError("response_too_large")
        body.extend(chunk)
    return bytes(body)


def retry_after_seconds(value: str | None, *, max_seconds: float | None = MAX_RETRY_AFTER_SECONDS) -> float | None:
    """Parse delta-seconds or an HTTP-date. Missing, unusable, or over-long values return None."""

    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    delay = max(0.0, delay)
    return None if max_seconds is not None and delay > max_seconds else delay
