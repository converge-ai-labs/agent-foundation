import asyncio
import json

import httpx
import httpx2
import pytest
from a13n_harness.capabilities.mem0_backends import (
    Mem0OSSBackend,
    Mem0PaginationUnsupported,
    Mem0Subject,
    added_memory_id,
    open_mem0_oss,
    open_mem0_platform,
)
from mem0 import AsyncMemoryClient

pytestmark = pytest.mark.anyio

SUBJECTS = (
    Mem0Subject("run_id", "thread-1"),
    Mem0Subject("agent_id", "agent-1"),
    Mem0Subject("user_id", "user-1"),
)


async def test_oss_uses_only_native_wire_shapes_and_bounded_listing():
    calls = []

    def handle(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, dict(request.url.params), body))
        return httpx2.Response(200, json={"results": []})

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        backend = Mem0OSSBackend(client)
        await backend.search("query", subjects=(SUBJECTS[0],), limit=7, threshold=0.4)
        await backend.add("  Exact text  ", subject=SUBJECTS[0])
        result = await backend.list(SUBJECTS[0], limit=1000)
        assert result == {"results": []}
        with pytest.raises(Mem0PaginationUnsupported):
            await backend.list(SUBJECTS[0], limit=1000, cursor="invented-page")
        assert calls == [
            (
                "POST",
                "/search",
                {},
                {"query": "query", "filters": {"run_id": "thread-1"}, "top_k": 7, "threshold": 0.4},
            ),
            (
                "POST",
                "/memories",
                {},
                {"messages": [{"role": "user", "content": "  Exact text  "}], "run_id": "thread-1", "infer": False},
            ),
            ("GET", "/memories", {"run_id": "thread-1", "top_k": "1000"}, None),
        ]


async def test_oss_union_runs_scopes_concurrently_and_deduplicates_top_k():
    entered = []
    ready = asyncio.Event()

    async def handle(request):
        body = json.loads(request.content)
        subject = next(iter(body["filters"]))
        assert len(body["filters"]) == 1 and "OR" not in body["filters"]
        entered.append(subject)
        if len(entered) == 3:
            ready.set()
        await ready.wait()
        score = {"run_id": 0.8, "agent_id": 0.95, "user_id": 0.7}[subject]
        return httpx2.Response(
            200,
            json={
                "results": [
                    {"id": "shared", "memory": "common", "score": score},
                    {"id": subject, "memory": subject, "score": 0.6},
                ]
            },
        )

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        async with asyncio.timeout(1):
            response = await Mem0OSSBackend(client).search("query", subjects=SUBJECTS, limit=3)
    assert [item["id"] for item in response["results"]] == ["shared", "agent_id", "run_id"]
    assert response["results"][0]["score"] == 0.95


async def test_oss_union_failure_cancels_siblings_instead_of_returning_partial_results():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handle(request):
        body = json.loads(request.content)
        if "run_id" in body["filters"]:
            await started.wait()
            return httpx2.Response(503)
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        with pytest.raises(ExceptionGroup):
            await Mem0OSSBackend(client).search("query", subjects=SUBJECTS[:2], limit=3)
    assert cancelled.is_set()


async def test_oss_union_shares_one_deadline():
    cancelled = []

    async def handle(request):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(request)

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.02):
                await Mem0OSSBackend(client).search("query", subjects=SUBJECTS, limit=3)
    assert len(cancelled) == 3


@pytest.mark.parametrize("subjects", [(), (*SUBJECTS, Mem0Subject("run_id", "other")), (SUBJECTS[0], SUBJECTS[0])])
async def test_oss_rejects_unbounded_or_empty_scope_queries(subjects):
    async with httpx2.AsyncClient(base_url="http://oss/") as client:
        with pytest.raises(ValueError):
            await Mem0OSSBackend(client).search("query", subjects=subjects, limit=3)


@pytest.mark.parametrize(
    "response", [{}, {"status": "queued"}, {"results": []}, {"results": [{"id": "id", "event": "PENDING"}]}]
)
async def test_queued_or_unknown_add_is_not_a_persisted_memory(response):
    with pytest.raises(ValueError):
        added_memory_id(response)


async def test_platform_native_sdk_contract_without_constructor_network_io(monkeypatch):
    monkeypatch.setenv("MEM0_TELEMETRY", "false")

    def eager_validation(_client):
        pytest.fail("The native SDK synchronous ping must not run")

    monkeypatch.setattr(AsyncMemoryClient, "_validate_api_key", eager_validation)
    calls = []

    def handle(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, dict(request.url.params), body))
        return httpx.Response(200, json={"results": [], "next": "https://never-follow.invalid/page"})

    async with open_mem0_platform(api_key="test-key", base_url="http://platform") as backend:
        sdk = backend.client
        assert sdk.org_id is None and sdk.project_id is None
        await sdk.async_client.aclose()
        sdk.async_client = httpx.AsyncClient(base_url="http://platform", transport=httpx.MockTransport(handle))
        await backend.search("query", subjects=SUBJECTS, limit=3)
        page = await backend.list(SUBJECTS[0], limit=2, cursor="2")
        assert page["next_cursor"] == "3"
        await backend.add("memory", subject=SUBJECTS[0])
        await backend.list(SUBJECTS[0], limit=1000)
    assert sdk.async_client.is_closed
    assert calls == [
        (
            "POST",
            "/v3/memories/search/",
            {},
            {"query": "query", "filters": {"OR": [subject.filter() for subject in SUBJECTS]}, "top_k": 3},
        ),
        ("POST", "/v3/memories/", {"page": "2", "page_size": "2"}, {"filters": {"run_id": "thread-1"}}),
        (
            "POST",
            "/v3/memories/add/",
            {},
            {"messages": [{"role": "user", "content": "memory"}], "filters": {"run_id": "thread-1"}, "infer": False},
        ),
        ("POST", "/v3/memories/", {"page": "1", "page_size": "200"}, {"filters": {"run_id": "thread-1"}}),
    ]
    assert "deferred" not in json.dumps(calls)


async def test_oss_transport_lifetime_is_owned_by_host_even_on_cancellation():
    client = None
    with pytest.raises(asyncio.CancelledError):
        async with open_mem0_oss(base_url="http://oss", api_key="test-key") as backend:
            client = backend.client
            assert not client.is_closed
            raise asyncio.CancelledError
    assert client.is_closed
