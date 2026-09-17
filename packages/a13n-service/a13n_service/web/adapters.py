"""Bounded first-party Web Provider adapters over fixed official destinations."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal

import httpx2
from a13n_harness.capabilities.web import (
    WebPolicy,
    WebProviderError,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
)
from anyio import move_on_after
from pydantic import BaseModel

from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.provider_plugins.api import WebProviderResponseError

from .domain import ScrapeSelection, SearchSelection
from .providers import SCRAPE, SEARCH


def provider_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(timeout=30, follow_redirects=False, trust_env=False)


class WebProviderTransport:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx2.AsyncClient] = provider_client,
        endpoint_policy: EndpointPolicy | None = None,
    ) -> None:
        self._clients = client_factory
        self._policy = endpoint_policy or EndpointPolicy(require_https=True)

    async def search(
        self, provider_type: str, credential: str, request: WebSearchRequest, selection: SearchSelection
    ) -> WebSearchResponse:
        adapter = SEARCH.get(provider_type)
        if adapter is None:
            raise WebProviderError("web_search_unavailable")
        return await adapter(self, credential, request, selection)

    async def search_registered(
        self,
        provider_type: str,
        *,
        configuration: object,
        credentials: object,
        request: WebSearchRequest,
        max_results: int,
        allow_domains: tuple[str, ...],
        deny_domains: tuple[str, ...],
    ) -> WebSearchResponse:
        del configuration
        selection = SearchSelection.model_construct(
            provider_id="wprov_builtin",
            max_results=max_results,
            allow_domains=allow_domains,
            deny_domains=deny_domains,
        )
        credential = "" if provider_type == "duckduckgo" else _api_key(credentials)
        return await self.search(provider_type, credential, request, selection)

    async def scrape(
        self, provider_type: str, credential: str, request: WebScrapeRequest, selection: ScrapeSelection
    ) -> WebScrapeResult:
        adapter = SCRAPE.get(provider_type)
        if adapter is None:
            raise WebProviderError("web_scrape_unavailable")
        if selection.restricted:
            raise WebProviderError("web_scrape_domain_restrictions_unsupported")
        return await adapter(self, credential, request, selection)

    async def scrape_registered(
        self,
        provider_type: str,
        *,
        configuration: object,
        credentials: object,
        request: WebScrapeRequest,
        policy: WebPolicy,
        max_content_bytes: int,
    ) -> WebScrapeResult:
        del configuration, policy
        selection = ScrapeSelection.model_construct(
            provider_id="wprov_builtin",
            max_content_bytes=max_content_bytes,
            allow_domains=(),
            deny_domains=(),
        )
        return await self.scrape(provider_type, _api_key(credentials), request, selection)

    async def exchange_json(
        self,
        build_request: Callable[[httpx2.AsyncClient], httpx2.Request],
        *,
        endpoint: str,
        operation: Literal["search", "scrape"],
        max_response_bytes: int,
    ) -> object:
        content = await self.exchange(
            build_request,
            endpoint=endpoint,
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
        endpoint: str,
        operation: Literal["search", "scrape"],
        max_response_bytes: int,
    ) -> bytes:
        failure_code = f"web_{operation}_failed"
        response_invalid_code = f"web_{operation}_response_invalid"
        client: httpx2.AsyncClient | None = None
        response: httpx2.Response | None = None
        try:
            await self._policy.validate(endpoint)
            client = self._clients()
            outgoing = build_request(client)
            response = await client.send(outgoing, stream=True, follow_redirects=False)
            if len(response.headers) > 128 or sum(len(k) + len(v) for k, v in response.headers.raw) > 64 * 1024:
                raise WebProviderError(response_invalid_code)
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                content.extend(chunk)
                if len(content) > max_response_bytes:
                    raise WebProviderError(response_invalid_code)
            if response.status_code != 200:
                raise WebProviderResponseError(
                    _failure_code(response.status_code, operation=operation),
                    retry_after=_retry_after(response.headers.get("Retry-After")),
                )
            return bytes(content)
        except httpx2.TimeoutException as error:
            raise TimeoutError from error
        except (httpx2.HTTPError, EndpointPolicyError, UnicodeError) as error:
            raise WebProviderError(failure_code) from error
        except (ValueError, TypeError, KeyError) as error:
            raise WebProviderError(response_invalid_code) from error
        finally:
            if response is not None:
                with move_on_after(1, shield=True):
                    await response.aclose()
            if client is not None:
                with move_on_after(1, shield=True):
                    await client.aclose()


class BoundWebProviderRuntime:
    """Operation-scoped adapter for one built-in Provider type."""

    def __init__(self, provider_type: str, transport: WebProviderTransport | None = None) -> None:
        self._provider_type = provider_type
        self._transport = transport or WebProviderTransport()

    async def search(
        self,
        *,
        configuration: BaseModel,
        credentials: BaseModel,
        request: WebSearchRequest,
        max_results: int,
        allow_domains: tuple[str, ...],
        deny_domains: tuple[str, ...],
    ) -> WebSearchResponse:
        return await self._transport.search_registered(
            self._provider_type,
            configuration=configuration,
            credentials=credentials,
            request=request,
            max_results=max_results,
            allow_domains=allow_domains,
            deny_domains=deny_domains,
        )

    async def scrape(
        self,
        *,
        configuration: BaseModel,
        credentials: BaseModel,
        request: WebScrapeRequest,
        policy: WebPolicy,
        max_content_bytes: int,
    ) -> WebScrapeResult:
        return await self._transport.scrape_registered(
            self._provider_type,
            configuration=configuration,
            credentials=credentials,
            request=request,
            policy=policy,
            max_content_bytes=max_content_bytes,
        )

    async def aclose(self) -> None:
        pass


def _api_key(credentials: object) -> str:
    if not isinstance(credentials, BaseModel):
        raise WebProviderError("web_provider_unavailable")
    value = credentials.model_dump().get("api_key")
    if not isinstance(value, str):
        raise WebProviderError("web_provider_unavailable")
    return value


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


def _retry_after(value: str | None) -> float:
    if value is None:
        return 1.0
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return math.inf
    return max(0, delay) if math.isfinite(delay) else math.inf
