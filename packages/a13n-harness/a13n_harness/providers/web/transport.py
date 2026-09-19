"""Bounded first-party Web Provider adapters over fixed official destinations."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx2
from a13n_logging import get_logger
from anyio import move_on_after

from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_harness.providers.http import ProviderHttpError, bounded_response_body, retry_after_seconds
from a13n_harness.providers.web.contracts import (
    WebProviderError,
)

from .errors import WebProviderResponseError

_logger = get_logger(__name__)


async def _close(close: Callable[[], Awaitable[None]]) -> None:
    try:
        with move_on_after(1, shield=True) as scope:
            await close()
        if scope.cancel_called:
            _logger.warning("Web transport cleanup timed out")
    except Exception as error:
        _logger.warning("Web transport cleanup failed", extra={"cleanup_error_type": type(error).__name__})


def provider_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(timeout=30, follow_redirects=False, trust_env=False)


class WebProviderTransport:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx2.AsyncClient] = provider_client,
        endpoint_policy: EndpointPolicy | None = None,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._client = client
        self._clients = client_factory
        self._policy = endpoint_policy or EndpointPolicy(require_https=True)

    async def exchange_json(
        self,
        build_request: Callable[[httpx2.AsyncClient], httpx2.Request],
        *,
        operation: Literal["search", "scrape"],
        max_response_bytes: int,
    ) -> object:
        content = await self.exchange(
            build_request,
            operation=operation,
            max_response_bytes=max_response_bytes,
        )
        try:
            return json.loads(content)
        except (ValueError, UnicodeError) as error:
            raise WebProviderError(f"web_{operation}_response_invalid") from error

    async def exchange(
        self,
        build_request: Callable[[httpx2.AsyncClient], httpx2.Request],
        *,
        operation: Literal["search", "scrape"],
        max_response_bytes: int,
    ) -> bytes:
        failure_code = f"web_{operation}_failed"
        response_invalid_code = f"web_{operation}_response_invalid"
        client: httpx2.AsyncClient | None = None
        response: httpx2.Response | None = None
        try:
            client = self._client if self._client is not None else self._clients()
            outgoing = build_request(client)
            # Generated vendor requests may carry query credentials (e.g. SerpApi).
            # Validate the actual destination, retaining path, userinfo and fragment
            # checks; configuration validation still rejects secret query inputs.
            await self._policy.validate(str(outgoing.url.copy_with(query=None)))
            response = await client.send(outgoing, stream=True, follow_redirects=False)
            if len(response.headers) > 128 or sum(len(k) + len(v) for k, v in response.headers.raw) > 64 * 1024:
                raise WebProviderError(response_invalid_code)
            content = await bounded_response_body(response, max_bytes=max_response_bytes)
            if response.status_code != 200:
                raise WebProviderResponseError(
                    _failure_code(response.status_code, operation=operation),
                    retry_after_seconds=retry_after_seconds(response.headers.get("Retry-After"), max_seconds=None),
                )
            return content
        except httpx2.TimeoutException as error:
            raise TimeoutError from error
        except ProviderHttpError as error:
            raise WebProviderError(response_invalid_code) from error
        except (httpx2.HTTPError, EndpointPolicyError, UnicodeError) as error:
            raise WebProviderError(failure_code) from error
        except (ValueError, TypeError, KeyError) as error:
            raise WebProviderError(response_invalid_code) from error
        finally:
            if response is not None:
                await _close(response.aclose)
            if client is not None and self._client is None:
                await _close(client.aclose)


def _failure_code(status: int, *, operation: Literal["search", "scrape"] = "search") -> str:
    prefix = f"web_{operation}"
    return {
        400: f"{prefix}_request_invalid",
        422: f"{prefix}_request_invalid",
        401: f"{prefix}_authentication_failed",
        403: f"{prefix}_authentication_failed",
        402: f"{prefix}_quota_exceeded",
        429: f"{prefix}_rate_limited",
        502: f"{prefix}_unavailable",
        503: f"{prefix}_unavailable",
        504: f"{prefix}_unavailable",
    }.get(status, f"{prefix}_failed")
