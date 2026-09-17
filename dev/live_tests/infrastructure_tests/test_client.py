"""Retained presentation availability is independent of live stream closure."""

import httpx2
import pytest

from ..infrastructure.client import LiveClient


def display_page(*, finalized=True, items=(), next_cursor=None, complete=True, reason=None, version=3):
    return {
        "items": list(items),
        "next_cursor": next_cursor,
        "snapshot_version": version,
        "projection_cursor": "10-0",
        "finalized": finalized,
        "complete": complete,
        "incomplete_reason": reason,
    }


@pytest.mark.anyio
async def test_retained_items_waits_for_finalization_and_reads_one_consistent_collection():
    requests = []
    responses = [
        httpx2.Response(409, json={"error": {"code": "items_unavailable"}}),
        httpx2.Response(200, json=display_page(finalized=False, version=1)),
        httpx2.Response(
            200,
            json=display_page(
                finalized=False,
                version=2,
                items=[
                    {"id": "item_1", "state": "in_progress", "content": {"text": "partial"}},
                ],
            ),
        ),
        httpx2.Response(
            200,
            json=display_page(
                items=[
                    {"id": "item_1", "state": "completed", "content": {"text": "complete"}},
                ],
                next_cursor="page-2",
            ),
        ),
        httpx2.Response(200, json=display_page(items=[{"id": "item_2"}])),
    ]

    def response(request):
        requests.append(request)
        return responses.pop(0)

    async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.MockTransport(response)) as http:
        items = await LiveClient({"timeout_seconds": 2}, http).retained_items("run_1")
    assert items == [
        {"id": "item_1", "state": "completed", "content": {"text": "complete"}},
        {"id": "item_2"},
    ]
    assert [request.url.params.get("cursor") for request in requests] == [None, None, None, None, "page-2"]


@pytest.mark.anyio
async def test_retained_items_rejects_finalized_incomplete_history():
    requests = []

    def response(request):
        requests.append(request)
        return httpx2.Response(200, json=display_page(complete=False, reason="source_discontinuity"))

    async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.MockTransport(response)) as http:
        with pytest.raises(AssertionError, match="incomplete final display: source_discontinuity"):
            await LiveClient({}, http).retained_items("run_1")
    assert len(requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("changed", [{"snapshot_version": 4}, {"projection_cursor": "11-0"}, {"finalized": False}])
async def test_retained_items_rejects_inconsistent_final_pages(changed):
    responses = [
        display_page(items=[{"id": "item_1"}], next_cursor="page-2"),
        {**display_page(items=[{"id": "item_2"}]), **changed},
    ]

    def response(request):
        return httpx2.Response(200, json=responses.pop(0))

    async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.MockTransport(response)) as http:
        with pytest.raises(AssertionError, match=r"coverage changed|incomplete final display"):
            await LiveClient({}, http).retained_items("run_1")


@pytest.mark.anyio
async def test_retained_items_accepts_a_finalized_empty_snapshot():
    def response(request):
        return httpx2.Response(200, json=display_page())

    async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.MockTransport(response)) as http:
        assert await LiveClient({}, http).retained_items("run_1") == []


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
@pytest.mark.parametrize("published", [False, True])
async def test_retained_items_never_finalized_fails_with_a_deadline(published):
    def response(request):
        if published:
            return httpx2.Response(200, json=display_page(finalized=False))
        return httpx2.Response(409, json={"error": {"code": "items_unavailable"}})

    async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.MockTransport(response)) as http:
        with pytest.raises(AssertionError, match="retained Items finalization: run_1"):
            await LiveClient({"timeout_seconds": 0.01}, http).retained_items("run_1")
