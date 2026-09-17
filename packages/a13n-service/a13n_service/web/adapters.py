"""Bounded first-party Web Provider adapters over fixed official destinations."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
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
    WebSearchResult,
)
from a13n_harness.usage import ProviderUsage
from anyio import move_on_after
from pydantic import BaseModel

from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.ids import new_object_id
from a13n_service.provider_plugins.api import WebProviderResponseError

from .domain import MAX_SCRAPE_CONTENT_BYTES, ScrapeSelection, SearchSelection

SEARCH_ENDPOINTS = {"brave": "https://api.search.brave.com/res/v1/web/search", "exa": "https://api.exa.ai/search"}
SCRAPE_ENDPOINTS = {"exa": "https://api.exa.ai/contents"}
SEARCH_RESPONSE_BYTES = 1024 * 1024
# JSON may encode one UTF-8 output byte as a six-byte `\u00XX` escape.
# The fixed allowance bounds the remaining result envelope and unexpected fields.
SCRAPE_RESPONSE_OVERHEAD_BYTES = 256 * 1024
MAX_JSON_BYTES_PER_CONTENT_BYTE = 6


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
        endpoint = SEARCH_ENDPOINTS.get(provider_type)
        if endpoint is None:
            raise WebProviderError("web_search_unavailable")
        limit = min(request.limit, selection.max_results)
        if provider_type == "brave" and (len(request.query) > 600 or len(request.query.split()) > 75):
            raise WebProviderError("web_search_request_invalid")
        if provider_type == "brave":

            def build_request(client: httpx2.AsyncClient) -> httpx2.Request:
                return client.build_request(
                    "GET",
                    endpoint,
                    headers={"X-Subscription-Token": credential, "Accept": "application/json"},
                    params={
                        "q": request.query,
                        "count": limit,
                        "result_filter": "web",
                        "text_decorations": "false",
                    },
                )
        else:
            body: dict[str, object] = {
                "query": request.query,
                "numResults": limit,
                "type": "auto",
                "contents": {"text": False, "highlights": True},
            }
            if selection.allow_domains:
                body["includeDomains"] = list(selection.allow_domains)
            if selection.deny_domains:
                body["excludeDomains"] = list(selection.deny_domains)

            def build_request(client: httpx2.AsyncClient) -> httpx2.Request:
                return client.build_request("POST", endpoint, headers={"x-api-key": credential}, json=body)

        payload = await self.exchange_json(
            build_request,
            endpoint=endpoint,
            operation="search",
            max_response_bytes=SEARCH_RESPONSE_BYTES,
        )
        try:
            return normalize_search_response(provider_type, payload, selection, limit=limit)
        except (ValueError, TypeError, KeyError) as error:
            raise WebProviderError("web_search_response_invalid") from error

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
        if provider_type not in SEARCH_ENDPOINTS:
            from .vendor_adapters import search

            return await search(
                self, provider_type, _api_key(credentials) if provider_type != "duckduckgo" else "", request, selection
            )
        credential = _api_key(credentials)
        return await self.search(provider_type, credential, request, selection)

    async def scrape(
        self, provider_type: str, credential: str, request: WebScrapeRequest, selection: ScrapeSelection
    ) -> WebScrapeResult:
        endpoint = SCRAPE_ENDPOINTS.get(provider_type)
        if endpoint is None:
            raise WebProviderError("web_scrape_unavailable")
        if selection.restricted:
            raise WebProviderError("web_scrape_domain_restrictions_unsupported")
        limit = min(request.max_content_bytes, selection.max_content_bytes)

        def build_request(client: httpx2.AsyncClient) -> httpx2.Request:
            return client.build_request(
                "POST",
                endpoint,
                headers={"x-api-key": credential},
                json={"urls": [request.url], "text": {"maxCharacters": limit + 1}},
            )

        payload = await self.exchange_json(
            build_request,
            endpoint=endpoint,
            operation="scrape",
            max_response_bytes=_scrape_response_bytes(limit),
        )
        try:
            return normalize_scrape_response(payload, request, selection)
        except (ValueError, TypeError, KeyError) as error:
            raise WebProviderError("web_scrape_response_invalid") from error

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
        if provider_type not in SCRAPE_ENDPOINTS:
            from .vendor_adapters import scrape

            return await scrape(self, provider_type, _api_key(credentials), request, selection)
        credential = _api_key(credentials)
        return await self.scrape(provider_type, credential, request, selection)

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


def _scrape_response_bytes(content_bytes: int) -> int:
    if not 1 <= content_bytes <= MAX_SCRAPE_CONTENT_BYTES:
        raise ValueError("scrape content budget is invalid")
    return SCRAPE_RESPONSE_OVERHEAD_BYTES + (content_bytes + 1) * MAX_JSON_BYTES_PER_CONTENT_BYTE


def normalize_search_response(
    provider_type: str, payload: object, selection: SearchSelection, *, limit: int
) -> WebSearchResponse:
    if not isinstance(payload, dict):
        raise ValueError("response must be an object")
    if provider_type == "brave":
        web = payload.get("web")
        if web is None and payload.get("type") == "search":
            raw = []
        elif isinstance(web, dict):
            raw = web.get("results")
        else:
            raise ValueError("missing web response")
    else:
        raw = payload.get("results")
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError("invalid search results")
    results: list[WebSearchResult] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("invalid search result")
        snippet = item.get("description") if provider_type == "brave" else item.get("highlights")
        if provider_type == "exa" and snippet is not None:
            if not isinstance(snippet, list) or not all(isinstance(part, str) for part in snippet):
                raise ValueError("invalid highlights")
            snippet = "\n".join(snippet)
        result = WebSearchResult(title=item.get("title") or "", url=item["url"], snippet=snippet or "")
        if not selection.allows(result.url):
            continue
        results.append(result)
    usage = _usage(payload, product="search") if provider_type == "exa" else ()
    return WebSearchResponse(results=tuple(results[:limit]), usage=usage)


def normalize_scrape_response(
    payload: object, request: WebScrapeRequest, selection: ScrapeSelection
) -> WebScrapeResult:
    if not isinstance(payload, dict):
        raise ValueError("response must be an object")
    raw = payload.get("results")
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise ValueError("invalid scrape result")
    item = raw[0]
    content = item.get("text")
    if not isinstance(content, str):
        raise ValueError("invalid scrape content")
    source_url = item.get("url") or request.url
    if not isinstance(source_url, str):
        raise ValueError("invalid scrape source URL")
    encoded = content.encode("utf-8")
    limit = min(request.max_content_bytes, selection.max_content_bytes)
    truncated = len(encoded) > limit
    if truncated:
        content = encoded[:limit].decode("utf-8", errors="ignore")
    title = item.get("title")
    if title is not None and not isinstance(title, str):
        raise ValueError("invalid scrape title")
    return WebScrapeResult(
        content=content,
        source_url=request.url,
        canonical_url=source_url,
        title=title,
        truncated=truncated,
        usage=_usage(payload, product="contents"),
    )


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


def _usage(payload: dict[str, object], *, product: str) -> tuple[ProviderUsage, ...]:
    costs = payload.get("costDollars")
    if not isinstance(costs, dict) or type(costs.get("total")) not in {int, float}:
        return ()
    cost = Decimal(str(costs["total"]))
    if not cost.is_finite() or cost < 0:
        return ()
    return (
        ProviderUsage(
            usage_id=new_object_id("usage"),
            provider="exa",
            product=product,
            timestamp=datetime.now(UTC),
            cost=cost,
            currency="USD",
        ),
    )


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
