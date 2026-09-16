import asyncio
import json

import httpx
import httpx2
import pytest
from a13n_harness.capabilities.mem0_backends import (
    Mem0OSSBackend,
    open_mem0_oss,
    open_mem0_platform,
)
from a13n_harness.memory import (
    MemoryDocumentScope,
    MemoryPage,
    MemoryPaginationUnsupported,
    MemoryScope,
    MemorySubject,
    MemoryWriteUnconfirmed,
)
from mem0 import AsyncMemoryClient

pytestmark = pytest.mark.anyio

SUBJECTS = (
    MemorySubject(MemoryScope.THREAD, "thread-1"),
    MemorySubject(MemoryScope.AGENT, "agent-1"),
    MemorySubject(MemoryScope.USER, "user-1"),
)


async def test_oss_uses_only_native_wire_shapes_and_bounded_listing():
    calls = []

    def handle(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, dict(request.url.params), body))
        if request.method == "POST" and request.url.path == "/memories":
            return httpx2.Response(200, json={"results": [{"id": "memory-1", "event": "ADD"}]})
        if request.url.path == "/memories/memory-1":
            return httpx2.Response(200, json={"id": "memory-1", "memory": "  Exact text  ", "run_id": "thread-1"})
        return httpx2.Response(200, json={"results": []})

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        backend = Mem0OSSBackend(client)
        await backend.search("query", subjects=(SUBJECTS[0],), limit=7, threshold=0.4)
        await backend.add("  Exact text  ", subject=SUBJECTS[0])
        result = await backend.list(SUBJECTS[0], limit=1000)
        assert result == MemoryPage(())
        with pytest.raises(MemoryPaginationUnsupported):
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
            ("GET", "/memories/memory-1", {}, None),
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
                    {"id": "shared", "memory": "common", "score": score, **body["filters"]},
                    {"id": subject, "memory": subject, "score": 0.6, **body["filters"]},
                ]
            },
        )

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        async with asyncio.timeout(1):
            response = await Mem0OSSBackend(client).search("query", subjects=SUBJECTS, limit=3)
    assert [item.id for item in response] == ["shared", "agent_id", "run_id"]
    assert response[0].score == 0.95


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


@pytest.mark.parametrize(
    "subjects", [(), (*SUBJECTS, MemorySubject(MemoryScope.THREAD, "other")), (SUBJECTS[0], SUBJECTS[0])]
)
async def test_oss_rejects_unbounded_or_empty_scope_queries(subjects):
    async with httpx2.AsyncClient(base_url="http://oss/") as client:
        with pytest.raises(ValueError):
            await Mem0OSSBackend(client).search("query", subjects=subjects, limit=3)


@pytest.mark.parametrize(
    "response", [{}, {"status": "queued"}, {"results": []}, {"results": [{"id": "id", "event": "PENDING"}]}]
)
async def test_queued_or_unknown_add_is_not_a_persisted_memory(response):
    def handle(request):
        return httpx2.Response(200, json=response)

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        with pytest.raises(MemoryWriteUnconfirmed):
            await Mem0OSSBackend(client).add("text", subject=SUBJECTS[0])


async def test_platform_native_sdk_contract_without_constructor_network_io(monkeypatch):
    monkeypatch.setenv("MEM0_TELEMETRY", "false")

    def eager_validation(_client):
        pytest.fail("The native SDK synchronous ping must not run")

    monkeypatch.setattr(AsyncMemoryClient, "_validate_api_key", eager_validation)
    calls = []

    def handle(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, dict(request.url.params), body))
        if request.url.path == "/v3/memories/add/":
            return httpx.Response(200, json={"results": [{"id": "memory-1", "event": "ADD"}]})
        if request.url.path.endswith("/memory-1/"):
            return httpx.Response(200, json={"id": "memory-1", "memory": "memory", "run_id": "thread-1"})
        return httpx.Response(200, json={"results": [], "next": "https://never-follow.invalid/page"})

    async with open_mem0_platform(api_key="test-key", base_url="http://platform") as backend:
        sdk = backend.client
        assert sdk.org_id is None and sdk.project_id is None
        await sdk.async_client.aclose()
        sdk.async_client = httpx.AsyncClient(base_url="http://platform", transport=httpx.MockTransport(handle))
        await backend.search("query", subjects=SUBJECTS, limit=3)
        page = await backend.list(SUBJECTS[0], limit=2, cursor="2")
        assert page.pagination.next_cursor == "3"
        await backend.add("memory", subject=SUBJECTS[0])
        await backend.list(SUBJECTS[0], limit=1000)
    assert sdk.async_client.is_closed
    assert calls == [
        (
            "POST",
            "/v3/memories/search/",
            {},
            {
                "query": "query",
                "filters": {"OR": [{"run_id": "thread-1"}, {"agent_id": "agent-1"}, {"user_id": "user-1"}]},
                "top_k": 3,
            },
        ),
        ("POST", "/v3/memories/", {"page": "2", "page_size": "2"}, {"filters": {"run_id": "thread-1"}}),
        (
            "POST",
            "/v3/memories/add/",
            {},
            {"messages": [{"role": "user", "content": "memory"}], "filters": {"run_id": "thread-1"}, "infer": False},
        ),
        ("GET", "/v1/memories/memory-1/", {}, None),
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


@pytest.mark.parametrize("kind", ["oss", "platform"])
async def test_native_crud_confirms_exact_text_and_checks_subject_before_mutation(kind):
    from contextlib import asynccontextmanager

    from a13n_harness.memory import MemoryRecordNotFound

    calls = []
    stored = {}

    def handle(request):
        response_type = httpx2.Response if kind == "oss" else httpx.Response
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.method == "POST":
            text = body["messages"][0]["content"]
            subjects = body["filters"] if kind == "platform" else {"run_id": body["run_id"]}
            assert body["infer"] is False
            stored["memory-1"] = {"id": "memory-1", "memory": text, **subjects}
            return response_type(200, json={"results": [{"id": "memory-1", "event": "ADD"}]})
        if request.method == "GET":
            return (
                response_type(200, json=stored["memory-1"])
                if stored
                else response_type(404, json={"detail": "not found"})
            )
        if request.method in {"PUT", "PATCH"}:
            stored["memory-1"]["memory"] = body["text"]
            return response_type(200, json={"message": "updated"})
        if request.method == "DELETE":
            stored.clear()
            return response_type(200, json={"message": "deleted"})
        pytest.fail("Unexpected native request")

    @asynccontextmanager
    async def backend_context():
        if kind == "oss":
            async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
                yield Mem0OSSBackend(client)
        else:
            async with open_mem0_platform(api_key="test-key", base_url="http://platform") as backend:
                await backend.client.async_client.aclose()
                backend.client.async_client = httpx.AsyncClient(
                    base_url="http://platform", transport=httpx.MockTransport(handle)
                )
                yield backend

    async with backend_context() as backend:
        record = await backend.add("  Exact original  ", subject=SUBJECTS[0])
        assert record.text == "  Exact original  "
        assert record.subjects == (SUBJECTS[0],)
        before = len(calls)
        for operation in (
            backend.get(record.id, subject=SUBJECTS[1]),
            backend.update(record.id, "forbidden", subject=SUBJECTS[1]),
            backend.delete(record.id, subject=SUBJECTS[1]),
        ):
            with pytest.raises(MemoryRecordNotFound):
                await operation
        assert all(method == "GET" for method, _, _ in calls[before:])
        updated = await backend.update(record.id, "  Exact update  ", subject=SUBJECTS[0])
        assert updated.text == "  Exact update  "
        await backend.delete(record.id, subject=SUBJECTS[0])
        with pytest.raises(MemoryRecordNotFound):
            await backend.get(record.id, subject=SUBJECTS[0])
    assert sum(method == "POST" for method, _, _ in calls) == 1
    assert sum(method in {"PUT", "PATCH"} for method, _, _ in calls) == 1
    assert sum(method == "DELETE" for method, _, _ in calls) == 1


@pytest.mark.parametrize("operation", ["add", "update", "delete"])
async def test_uncertain_native_writes_never_retry(operation):
    from a13n_harness.memory import MemoryWriteUnconfirmed

    writes = []

    def handle(request):
        if request.method != "GET":
            writes.append(request)
            return httpx2.Response(200, json={"results": [{"id": "memory-1", "event": "ADD"}]})
        # The native write was accepted, but readback does not confirm the outcome.
        return httpx2.Response(200, json={"id": "memory-1", "memory": "old text", "run_id": "thread-1"})

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        backend = Mem0OSSBackend(client)
        with pytest.raises(MemoryWriteUnconfirmed):
            if operation == "add":
                await backend.add("new text", subject=SUBJECTS[0])
            elif operation == "update":
                await backend.update("memory-1", "new text", subject=SUBJECTS[0])
            else:
                await backend.delete("memory-1", subject=SUBJECTS[0])
        assert len(writes) == 1


@pytest.mark.parametrize("text", ["", "  ", "a" * 8001, "invalid\ud800"])
async def test_invalid_explicit_text_fails_before_native_io(text):
    async with httpx2.AsyncClient(
        base_url="http://oss/", transport=httpx2.MockTransport(lambda _: pytest.fail("Unexpected I/O"))
    ) as client:
        with pytest.raises(ValueError):
            await Mem0OSSBackend(client).add(text, subject=SUBJECTS[0])


async def test_native_page_cannot_return_records_outside_the_requested_subject():
    from a13n_harness.memory import MemoryRecordNotFound

    def handle(request):
        return httpx2.Response(200, json={"results": [{"id": "other", "memory": "secret", "run_id": "other-thread"}]})

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        with pytest.raises(MemoryRecordNotFound):
            await Mem0OSSBackend(client).list(SUBJECTS[0], limit=10)


async def test_document_write_verifies_metadata_and_filters_exact_authorized_keys():
    subject = MemorySubject(MemoryDocumentScope.CONVERSATION, "conversation-namespace")
    saved = {}
    calls = []

    def handle(request):
        body = json.loads(request.content) if request.content else None
        calls.append(body)
        if request.url.path == "/memories" and request.method == "POST":
            saved.update(
                id="doc-native", memory=body["messages"][0]["content"], run_id=body["run_id"], metadata=body["metadata"]
            )
            assert body["infer"] is False
            return httpx2.Response(200, json={"results": [{"id": "doc-native", "event": "ADD"}]})
        if request.url.path == "/search":
            assert body["filters"] == {"run_id": subject.value, "record_key": {"in": ["doc-one"]}}
            return httpx2.Response(200, json={"results": [saved]})
        return httpx2.Response(200, json=saved)

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        backend = Mem0OSSBackend(client)
        record = await backend.add_document(
            "# Release\nSteps", subject=subject, metadata={"record_key": "doc-one", "activity_date": "2026-09-15"}
        )
        assert record.subjects == (subject,)
        assert record.metadata["record_key"] == "doc-one"
        assert await backend.search_documents("release", subject=subject, record_keys=("doc-one",), limit=5) == (
            record,
        )
        before = len(calls)
        assert await backend.search_documents("release", subject=subject, record_keys=(), limit=5) == ()
        assert len(calls) == before


async def test_document_metadata_mismatch_is_unconfirmed_without_retry():
    writes = 0

    def handle(request):
        nonlocal writes
        if request.method == "POST":
            writes += 1
            return httpx2.Response(200, json={"results": [{"id": "native", "event": "ADD"}]})
        return httpx2.Response(
            200,
            json={
                "id": "native",
                "memory": "body",
                "run_id": "scope",
                "metadata": {"a13n_scope": "conversation", "record_key": "wrong"},
            },
        )

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        with pytest.raises(MemoryWriteUnconfirmed):
            await Mem0OSSBackend(client).add_document(
                "body",
                subject=MemorySubject(MemoryDocumentScope.CONVERSATION, "scope"),
                metadata={"record_key": "expected"},
            )
    assert writes == 1


async def test_document_search_rejects_provider_filter_violation():
    def handle(request):
        return httpx2.Response(
            200,
            json={
                "results": [
                    {
                        "id": "native",
                        "memory": "body",
                        "run_id": "scope",
                        "metadata": {"a13n_scope": "conversation", "record_key": "not-authorized"},
                    }
                ]
            },
        )

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="outside the authorized"):
            await Mem0OSSBackend(client).search_documents(
                "query",
                subject=MemorySubject(MemoryDocumentScope.CONVERSATION, "scope"),
                record_keys=("allowed",),
                limit=5,
            )
