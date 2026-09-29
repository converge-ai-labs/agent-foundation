"""Caller-owned HTTP transport construction for native Model providers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import httpx2
from pydantic_ai.retries import AsyncHTTPX2TenacityTransport, RetryConfig, wait_retry_after
from tenacity import retry_if_exception, stop_after_attempt, wait_exponential

DEFAULT_MODEL_HTTP_RETRY_STATUS_CODES = frozenset({429, 502, 503, 504})


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
    # Model requests stream for minutes; connection setup does not.
    timeout: int = 600,
    connect: int = 5,
    transport: httpx2.AsyncBaseTransport | None = None,
    retry: ModelHttpRetryConfig | None = DEFAULT_MODEL_HTTP_RETRY_CONFIG,
) -> httpx2.AsyncClient:
    """Create a caller-owned provider client with transport retries and timeouts.

    The default retry policy follows Pydantic AI's HTTP retry guidance: retry
    transient transport errors and selected rate-limit/gateway statuses, respect
    ``Retry-After``, and fall back to bounded exponential backoff. Pass
    ``retry=None`` to disable automatic retries.

    Without an explicit transport, proxy routing follows httpx2's standard
    environment variables, including ``NO_PROXY``. Retries apply to both direct
    and proxied requests. An explicit transport retains control of routing.

    Request headers remain native ``ModelSettings.extra_headers``. The caller
    passes this client to a compatible provider and owns its lifecycle.
    """

    for name, value in (("timeout", timeout), ("connect", connect)):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")

    return _ModelHttpClient(
        timeout=httpx2.Timeout(timeout=timeout, connect=connect),
        transport=transport,
        retry=retry,
    )


class _ModelHttpClient(httpx2.AsyncClient):
    """Let httpx2 own proxy selection, then wrap every selected transport."""

    def __init__(
        self,
        *,
        timeout: httpx2.Timeout,
        transport: httpx2.AsyncBaseTransport | None,
        retry: ModelHttpRetryConfig | None,
    ) -> None:
        self._model_retry = retry
        # Passing a retry wrapper here would disable httpx2's environment proxies.
        super().__init__(timeout=timeout, transport=transport)

    # Keep the dependency's private construction hooks localized here. Delegating
    # all arguments preserves its TLS, proxy and pool defaults without copying
    # its environment parsing, NO_PROXY matching or transport lifecycle.
    def _init_transport(self, *args: Any, **kwargs: Any) -> httpx2.AsyncBaseTransport:
        return self._with_retry(super()._init_transport(*args, **kwargs))

    def _init_proxy_transport(self, *args: Any, **kwargs: Any) -> httpx2.AsyncBaseTransport:
        return self._with_retry(super()._init_proxy_transport(*args, **kwargs))

    def _with_retry(self, transport: httpx2.AsyncBaseTransport) -> httpx2.AsyncBaseTransport:
        retry = self._model_retry
        if retry is None:
            return transport
        return AsyncHTTPX2TenacityTransport(
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
