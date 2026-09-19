"""Caller-owned HTTP transport construction for native Model providers."""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx2
from pydantic_ai.retries import AsyncHTTPX2TenacityTransport, RetryConfig, wait_retry_after
from tenacity import retry_if_exception, stop_after_attempt, wait_exponential

DEFAULT_MODEL_HTTP_RETRY_STATUS_CODES = frozenset({429, 502, 503, 504})
# Model requests stream for minutes; connection setup does not.
DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS = 600
DEFAULT_MODEL_HTTP_CONNECT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True, slots=True)
class ModelHttpRetryConfig:
    """Bounded retry policy for transient model-provider HTTP failures."""

    attempts: int = 5
    backoff_multiplier: float = 1.0
    max_wait_seconds: float = 30.0
    retry_after_max_wait_seconds: float = 300.0
    status_codes: frozenset[int] = DEFAULT_MODEL_HTTP_RETRY_STATUS_CODES

    def __post_init__(self) -> None:
        if not isinstance(self.attempts, int) or isinstance(self.attempts, bool) or self.attempts <= 0:
            raise ValueError("attempts must be a positive integer")
        for name in ("backoff_multiplier", "max_wait_seconds", "retry_after_max_wait_seconds"):
            value = getattr(self, name)
            if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")
        status_codes = frozenset(self.status_codes)
        if any(
            not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599
            for status in status_codes
        ):
            raise ValueError("status_codes must contain valid HTTP status integers")
        object.__setattr__(self, "status_codes", status_codes)


DEFAULT_MODEL_HTTP_RETRY_CONFIG = ModelHttpRetryConfig()


def create_model_http_client(
    *,
    timeout: int = DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    connect: int = DEFAULT_MODEL_HTTP_CONNECT_TIMEOUT_SECONDS,
    transport: httpx2.AsyncBaseTransport | None = None,
    retry: ModelHttpRetryConfig | None = DEFAULT_MODEL_HTTP_RETRY_CONFIG,
) -> httpx2.AsyncClient:
    """Create a caller-owned provider client with transport retries and timeouts.

    The default retry policy follows Pydantic AI's HTTP retry guidance: retry
    transient transport errors and selected rate-limit/gateway statuses, respect
    ``Retry-After``, and fall back to bounded exponential backoff. Pass
    ``retry=None`` to disable automatic retries.

    Request headers remain native ``ModelSettings.extra_headers``. The caller
    passes this client to a compatible provider and owns its lifecycle.
    """

    for name, value in (("timeout", timeout), ("connect", connect)):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")

    resolved_transport = transport
    if retry is not None:
        resolved_transport = AsyncHTTPX2TenacityTransport(
            config=RetryConfig(
                retry=retry_if_exception(lambda error: _is_retryable_http_error(error, retry)),
                wait=wait_retry_after(
                    fallback_strategy=wait_exponential(
                        multiplier=retry.backoff_multiplier,
                        max=retry.max_wait_seconds,
                    ),
                    max_wait=retry.retry_after_max_wait_seconds,
                ),
                stop=stop_after_attempt(retry.attempts),
                reraise=True,
            ),
            wrapped=transport,
            validate_response=lambda response: _validate_retry_response(response, retry),
        )

    return httpx2.AsyncClient(
        timeout=httpx2.Timeout(timeout=timeout, connect=connect),
        transport=resolved_transport,
    )


def _is_retryable_http_error(error: BaseException, config: ModelHttpRetryConfig) -> bool:
    if isinstance(error, httpx2.HTTPStatusError):
        return error.response.status_code in config.status_codes
    return isinstance(error, httpx2.TimeoutException | httpx2.ConnectError | httpx2.ReadError)


def _validate_retry_response(response: httpx2.Response, config: ModelHttpRetryConfig) -> None:
    if response.status_code in config.status_codes:
        response.raise_for_status()


__all__ = [
    "DEFAULT_MODEL_HTTP_RETRY_CONFIG",
    "DEFAULT_MODEL_HTTP_RETRY_STATUS_CODES",
    "ModelHttpRetryConfig",
    "create_model_http_client",
]
