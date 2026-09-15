"""Retained presentation availability is independent of live stream closure."""

import httpx2
import pytest

from ..infrastructure.client import LiveClient


@pytest.mark.anyio
async def test_retained_items_waits_for_publication_and_reads_all_pages():
    requests = []

    def response(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(409, json={"error": {"code": "items_unavailable"}})
        if request.url.params.get("cursor") == "page-2":
            return httpx2.Response(200, json={"items": [{"id": "item_2"}], "next_cursor": None})
        return httpx2.Response(200, json={"items": [{"id": "item_1"}], "next_cursor": "page-2"})

    async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.MockTransport(response)) as http:
        client = LiveClient({"timeout_seconds": 1}, http)
        assert await client.retained_items("run_1") == [{"id": "item_1"}, {"id": "item_2"}]
    assert len(requests) == 4


@pytest.mark.anyio
@pytest.mark.parametrize("status,code", [(409, "different_conflict"), (500, "internal_error")])
async def test_retained_items_does_not_retry_unrelated_errors(status, code):
    requests = []

    def response(request):
        requests.append(request)
        return httpx2.Response(status, json={"error": {"code": code}})

    async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.MockTransport(response)) as http:
        with pytest.raises(AssertionError, match=f"HTTP {status}"):
            await LiveClient({}, http).retained_items("run_1")
    assert len(requests) == 1


@pytest.mark.anyio
async def test_retained_items_never_published_fails_with_a_deadline():
    def response(request):
        return httpx2.Response(409, json={"error": {"code": "items_unavailable"}})

    async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.MockTransport(response)) as http:
        with pytest.raises(AssertionError, match="retained Items publication: run_1"):
            await LiveClient({"timeout_seconds": 0.01}, http).retained_items("run_1")
