"""Keenable Search (API key optional)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness.providers.web.contracts import WebProviderError, WebSearchRequest, WebSearchResponse
from a13n_harness.providers.web.options import SearchOptions

from ..configuration import ApiKeyCredential, EmptyConfiguration
from .common import SEARCH_RESPONSE_BYTES, search_response

if TYPE_CHECKING:
    from a13n_harness.providers.web.transport import WebProviderTransport

PUBLIC_SEARCH_URL = "https://api.keenable.ai/v1/search/public"
SEARCH_URL = "https://api.keenable.ai/v1/search"
MAX_RESULTS = 50
SNIPPET_CHARS = 1000


async def search(
    configuration: EmptyConfiguration,
    credential: ApiKeyCredential | None,
    request: WebSearchRequest,
    options: SearchOptions,
    transport: WebProviderTransport,
) -> WebSearchResponse:
    """Search the keyless public endpoint, or the keyed endpoint when a credential is configured."""
    key = credential.api_key.get_secret_value() if credential is not None else None
    headers = {"X-Keenable-Title": "a13n"}
    if key:
        headers["X-API-Key"] = key
    limit = min(request.limit, options.max_results, MAX_RESULTS)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            SEARCH_URL if key else PUBLIC_SEARCH_URL,
            headers=headers,
            json={"query": request.query, "max_results": limit},
        ),
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict):
        raise WebProviderError("web_search_response_invalid")
    rows = payload.get("results")
    if isinstance(rows, list):
        rows = [row for row in rows if not isinstance(row, dict) or row.get("url")]
    return search_response(
        rows,
        lambda item: (
            item.get("title") or "",
            item["url"],
            item.get("description") or _snippet(item),
        ),
    )


def _snippet(item: dict[str, object]) -> str:
    snippet = item.get("snippet")
    if snippet is None:
        return ""
    if not isinstance(snippet, str):
        raise ValueError("invalid search snippet")
    return snippet[:SNIPPET_CHARS]
