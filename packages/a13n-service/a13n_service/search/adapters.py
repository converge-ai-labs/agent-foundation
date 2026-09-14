"""Bounded first-party HTTP search adapters over fixed official destinations."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx2
from a13n_harness.capabilities.web import WebProviderError, WebSearchRequest, WebSearchResponse, WebSearchResult
from a13n_harness.usage import ProviderUsage
from anyio import move_on_after

from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.ids import new_object_id

from .domain import SearchSelection, normalize_domain

ENDPOINTS = {"brave": "https://api.search.brave.com/res/v1/web/search", "exa": "https://api.exa.ai/search"}
MAX_RESPONSE_BYTES = 1024 * 1024


class SearchResponseError(WebProviderError):
    def __init__(self, code: str, *, retry_after: float | None = None) -> None:
        super().__init__(code)
        self.retry_after = retry_after


def search_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(timeout=30, follow_redirects=False, trust_env=False)


class SearchTransport:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx2.AsyncClient] = search_client,
        endpoint_policy: EndpointPolicy | None = None,
    ) -> None:
        self._clients = client_factory
        self._policy = endpoint_policy or EndpointPolicy(require_https=True)

    async def dispatch(
        self, provider_type: str, credential: str, request: WebSearchRequest, selection: SearchSelection
    ) -> WebSearchResponse:
        endpoint = ENDPOINTS.get(provider_type)
        if endpoint is None:
            raise WebProviderError("web_search_unavailable")
        limit = min(request.limit, selection.max_results)
        if provider_type == "brave" and (len(request.query) > 600 or len(request.query.split()) > 75):
            raise WebProviderError("web_search_request_invalid")
        try:
            await self._policy.validate(endpoint)
            client = self._clients()
            try:
                if provider_type == "brave":
                    outgoing = client.build_request(
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
                    if selection.include_domains:
                        body["includeDomains"] = list(selection.include_domains)
                    outgoing = client.build_request("POST", endpoint, headers={"x-api-key": credential}, json=body)
                response = await client.send(outgoing, stream=True, follow_redirects=False)
                try:
                    if len(response.headers) > 128 or sum(len(k) + len(v) for k, v in response.headers.raw) > 64 * 1024:
                        raise WebProviderError("web_search_response_invalid")
                    content = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise WebProviderError("web_search_response_invalid")
                    if response.status_code != 200:
                        raise SearchResponseError(
                            _failure_code(response.status_code),
                            retry_after=_retry_after(response.headers.get("Retry-After")),
                        )
                    return normalize_response(provider_type, json.loads(content), selection, limit=limit)
                finally:
                    with move_on_after(1, shield=True):
                        await response.aclose()
            finally:
                with move_on_after(1, shield=True):
                    await client.aclose()
        except httpx2.TimeoutException as error:
            raise TimeoutError from error
        except (httpx2.HTTPError, EndpointPolicyError, UnicodeError) as error:
            raise WebProviderError("web_search_failed") from error
        except (ValueError, TypeError, KeyError) as error:
            raise WebProviderError("web_search_response_invalid") from error


def normalize_response(
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
        if selection.include_domains:
            hostname = urlsplit(result.url).hostname
            try:
                hostname = normalize_domain(hostname or "")
            except ValueError:
                continue
            if not any(hostname == domain or hostname.endswith("." + domain) for domain in selection.include_domains):
                continue
        results.append(result)
    usage: tuple[ProviderUsage, ...] = ()
    costs = payload.get("costDollars")
    if provider_type == "exa" and isinstance(costs, dict) and type(costs.get("total")) in {int, float}:
        cost = Decimal(str(costs["total"]))
        if cost.is_finite() and cost >= 0:
            usage = (
                ProviderUsage(
                    usage_id=new_object_id("usage"),
                    provider="exa",
                    product="search",
                    timestamp=datetime.now(UTC),
                    cost=cost,
                    currency="USD",
                ),
            )
    return WebSearchResponse(results=tuple(results[:limit]), usage=usage)


def _failure_code(status: int) -> str:
    return {
        400: "web_search_request_invalid",
        422: "web_search_request_invalid",
        401: "web_search_authentication_failed",
        403: "web_search_authentication_failed",
        402: "web_search_quota_exceeded",
        429: "web_search_rate_limited",
        502: "web_search_unavailable",
        503: "web_search_unavailable",
        504: "web_search_unavailable",
    }.get(status, "web_search_failed")


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
