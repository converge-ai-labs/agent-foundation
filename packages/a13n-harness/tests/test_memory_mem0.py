"""mem0 record stores against fakes of the two REST APIs.

The request and response shapes follow the vendor references:

- Platform: https://docs.mem0.ai/api-reference/memory/add-memories (POST /v3/memories/add/),
  https://docs.mem0.ai/api-reference/memory/search-memories (POST /v3/memories/search/),
  https://docs.mem0.ai/api-reference/memory/get-memories (POST /v3/memories/?page=&page_size=),
  https://docs.mem0.ai/api-reference/memory/get-memory, .../update-memory and .../delete-memory
  (GET, PUT and DELETE /v1/memories/{id}/), and https://docs.mem0.ai/api-reference/memory/delete-memories
  (DELETE /v1/memories/?user_id=).
- Self-hosted server: https://docs.mem0.ai/open-source/features/rest-api (POST /memories, POST /search,
  GET /memories?user_id=&top_k=, GET, PUT and DELETE /memories/{id}, DELETE /memories?user_id=).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx2
import pytest
from a13n_harness.providers.memory import (
    BUILT_IN_MEMORY_PROVIDERS,
    MEM0_OSS,
    MEM0_PLATFORM,
    Mem0OSSConfiguration,
    Mem0PlatformConfiguration,
    MemoryStoreError,
    RecordStore,
)
from pydantic import ValidationError

pytestmark = pytest.mark.anyio

type Api = Literal["platform", "oss"]
BASE = "https://mem0.example/prefix"
NAMESPACE = "a13n-0f3c"


class FakeMem0:
    """A stateful stand-in for one mem0 API that records every request.

    `answer(request)` may return a response to replace the fake's own.
    """

    def __init__(self, api: Api) -> None:
        self.api = api
        self.rows: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx2.Request] = []
        self.answer: Callable[[httpx2.Request], httpx2.Response | None] = lambda request: None
        self.stored_text: Callable[[str], str] = lambda text: text

    def row(self, text: str, user_id: str = NAMESPACE) -> str:
        record_id = f"m{len(self.rows) + 1}"
        self.rows[record_id] = {
            "id": record_id,
            "memory": text,
            "user_id": user_id,
            "created_at": "2026-09-01T10:00:00-07:00",
            "updated_at": None,
        }
        return record_id

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if (answer := self.answer(request)) is not None:
            return answer
        path = request.url.path.removeprefix("/prefix")
        return self._platform(request, path) if self.api == "platform" else self._oss(request, path)

    def _platform(self, request: httpx2.Request, path: str) -> httpx2.Response:
        body = json.loads(request.content) if request.content else {}
        match request.method, path:
            case "POST", "/v3/memories/add/":
                record_id = self.row(self.stored_text(body["messages"][0]["content"]), body["user_id"])
                result = {"id": record_id, "data": {"memory": body["messages"][0]["content"]}, "event": "ADD"}
                return httpx2.Response(200, json={"status": "SUCCEEDED", "event_id": "e1", "results": [result]})
            case "POST", "/v3/memories/search/":
                rows = self._of(body["filters"]["user_id"])[: body["top_k"]]
                return httpx2.Response(200, json={"results": [{**row, "score": 0.8} for row in rows]})
            case "POST", "/v3/memories/":
                page, size = int(request.url.params["page"]), int(request.url.params["page_size"])
                rows = self._of(body["filters"]["user_id"])
                items = [{key: row[key] for key in ("id", "memory", "created_at", "updated_at")} for row in rows]
                more = f"{BASE}/v3/memories/?page={page + 1}" if page * size < len(rows) else None
                return httpx2.Response(
                    200,
                    json={
                        "count": len(rows),
                        "next": more,
                        "previous": None,
                        "results": items[(page - 1) * size :][:size],
                    },
                )
            case "DELETE", "/v1/memories/":
                self._purge(request.url.params["user_id"])
                return httpx2.Response(
                    200, json={"message": "Delete in progress. This may take some time.", "event_id": "e2"}
                )
        record_id = path.removeprefix("/v1/memories/").removesuffix("/")
        return self._record(request, record_id, missing=httpx2.Response(404, json={"error": "Memory not found!"}))

    def _oss(self, request: httpx2.Request, path: str) -> httpx2.Response:
        body = json.loads(request.content) if request.content else {}
        match request.method, path:
            case "POST", "/memories":
                record_id = self.row(self.stored_text(body["messages"][0]["content"]), body["user_id"])
                return httpx2.Response(
                    200, json={"results": [{"id": record_id, "memory": body["messages"][0]["content"], "event": "ADD"}]}
                )
            case "POST", "/search":
                rows = self._of(body["filters"]["user_id"])[: body["top_k"]]
                return httpx2.Response(200, json={"results": [{**row, "score": 0.8} for row in rows]})
            case "GET", "/memories":
                rows = self._of(request.url.params["user_id"])[: int(request.url.params["top_k"])]
                return httpx2.Response(200, json={"results": rows})
            case "DELETE", "/memories":
                self._purge(request.url.params["user_id"])
                return httpx2.Response(200, json={"message": "All relevant memories deleted"})
        return self._record(request, path.removeprefix("/memories/"), missing=httpx2.Response(200, json=None))

    def _record(self, request: httpx2.Request, record_id: str, *, missing: httpx2.Response) -> httpx2.Response:
        row = self.rows.get(record_id)
        if row is None:
            return missing if request.method == "GET" else httpx2.Response(404, json={"detail": "Memory not found"})
        if request.method == "PUT":
            row["memory"] = self.stored_text(json.loads(request.content)["text"])
            row["updated_at"] = "2026-09-02T10:00:00-07:00"
        elif request.method == "DELETE":
            del self.rows[record_id]
            return httpx2.Response(200, json={"message": "Memory deleted successfully!"})
        return httpx2.Response(200, json=row)

    def _of(self, user_id: str) -> list[dict[str, Any]]:
        return [row for row in self.rows.values() if row["user_id"] == user_id]

    def _purge(self, user_id: str) -> None:
        for record_id in [row["id"] for row in self._of(user_id)]:
            del self.rows[record_id]

    def calls(self) -> list[tuple[str, str]]:
        return [(request.method, request.url.path.removeprefix("/prefix")) for request in self.requests]


@asynccontextmanager
async def _store(
    fake: FakeMem0, *, credential: dict[str, str] | None = None, namespace: str = NAMESPACE
) -> AsyncIterator[RecordStore]:
    definition = MEM0_PLATFORM if fake.api == "platform" else MEM0_OSS
    if credential is None and fake.api == "platform":
        credential = {"api_key": "m0-secret"}
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(fake.handle)) as client:
        async with definition.open({"base_url": f"{BASE}/"}, credential, namespace=namespace, http=client) as store:
            yield store


def _json(request: httpx2.Request) -> Any:
    return json.loads(request.content)


async def _fails(call: Any) -> str:
    with pytest.raises(MemoryStoreError) as caught:
        await call
    return caught.value.code


async def test_platform_requests_follow_the_api_reference() -> None:
    fake = FakeMem0("platform")
    async with _store(fake) as store:
        added = await store.add("likes tea")
        await store.search("drinks", limit=3)
        await store.list(limit=2)
        await store.update(added.id, "likes coffee")
        await store.delete(added.id)
        await store.purge()

    assert fake.calls() == [
        ("POST", "/v3/memories/add/"),
        ("GET", "/v1/memories/m1/"),
        ("POST", "/v3/memories/search/"),
        ("POST", "/v3/memories/"),
        ("GET", "/v1/memories/m1/"),
        ("PUT", "/v1/memories/m1/"),
        ("GET", "/v1/memories/m1/"),
        ("GET", "/v1/memories/m1/"),
        ("DELETE", "/v1/memories/m1/"),
        ("GET", "/v1/memories/m1/"),
        ("DELETE", "/v1/memories/"),
    ]
    add, _, search, listing, _, update, *_, purge = fake.requests
    assert all(request.headers["authorization"] == "Token m0-secret" for request in fake.requests)
    assert _json(add) == {"messages": [{"role": "user", "content": "likes tea"}], "user_id": NAMESPACE, "infer": False}
    assert _json(search) == {"query": "drinks", "filters": {"user_id": NAMESPACE}, "top_k": 3}
    assert _json(listing) == {"filters": {"user_id": NAMESPACE}}
    assert dict(listing.url.params) == {"page": "1", "page_size": "2"}
    assert _json(update) == {"text": "likes coffee"}
    assert dict(purge.url.params) == {"user_id": NAMESPACE}


async def test_self_hosted_requests_follow_the_server_api() -> None:
    fake = FakeMem0("oss")
    async with _store(fake, credential={"api_key": "local-key"}) as store:
        added = await store.add("likes tea")
        await store.search("drinks", limit=3)
        await store.list(limit=2)
        await store.update(added.id, "likes coffee")
        await store.delete(added.id)
        await store.purge()

    assert fake.calls() == [
        ("POST", "/memories"),
        ("GET", "/memories/m1"),
        ("POST", "/search"),
        ("GET", "/memories"),
        ("GET", "/memories/m1"),
        ("PUT", "/memories/m1"),
        ("GET", "/memories/m1"),
        ("GET", "/memories/m1"),
        ("DELETE", "/memories/m1"),
        ("GET", "/memories/m1"),
        ("DELETE", "/memories"),
    ]
    add, _, search, listing, *_, purge = fake.requests
    assert all(request.headers["x-api-key"] == "local-key" for request in fake.requests)
    assert _json(add) == {"messages": [{"role": "user", "content": "likes tea"}], "user_id": NAMESPACE, "infer": False}
    assert _json(search) == {"query": "drinks", "filters": {"user_id": NAMESPACE}, "top_k": 3}
    assert dict(listing.url.params) == {"user_id": NAMESPACE, "top_k": "3"}
    assert dict(purge.url.params) == {"user_id": NAMESPACE}

    unauthenticated = FakeMem0("oss")
    async with _store(unauthenticated) as store:
        await store.search("drinks", limit=1)
    assert "x-api-key" not in unauthenticated.requests[0].headers


@pytest.mark.parametrize("api", ["platform", "oss"])
async def test_records_round_trip_within_the_namespace(api: Api) -> None:
    fake = FakeMem0(api)
    other = fake.row("another person's fact", user_id="someone-else")
    async with _store(fake) as store:
        tea = await store.add("likes tea")
        berlin = await store.add("works in Berlin")
        assert (tea.text, tea.updated_at is not None) == ("likes tea", True)
        updated = await store.update(tea.id, "likes coffee")
        assert updated.text == "likes coffee" and updated.updated_at is not None
        found = await store.search("drinks", limit=5)
        assert [(record.id, record.text, record.score) for record in found] == [
            (tea.id, "likes coffee", 0.8),
            (berlin.id, "works in Berlin", 0.8),
        ]
        await store.delete(berlin.id)
        assert [record.id for record in (await store.list(limit=10)).records] == [tea.id]
        await store.purge()
        assert (await store.list(limit=10)).records == ()
    assert list(fake.rows) == [other]


@pytest.mark.parametrize("api", ["platform", "oss"])
async def test_a_record_outside_the_namespace_is_not_found(api: Api) -> None:
    fake = FakeMem0(api)
    other = fake.row("another person's fact", user_id="someone-else")
    async with _store(fake) as store:
        assert await _fails(store.update(other, "mine now")) == "record_not_found"
        assert await _fails(store.delete(other)) == "record_not_found"
        assert await _fails(store.delete("m404")) == "record_not_found"
    assert all(request.method == "GET" for request in fake.requests)
    assert fake.rows[other]["memory"] == "another person's fact"


async def test_a_stray_record_of_another_namespace_is_dropped_from_results() -> None:
    fake = FakeMem0("oss")
    mine = fake.row("mine")
    theirs = fake.row("theirs", user_id="someone-else")
    fake.answer = lambda request: (
        httpx2.Response(200, json={"results": [fake.rows[mine], fake.rows[theirs]]})
        if request.url.path.endswith("/search")
        else None
    )
    async with _store(fake) as store:
        assert [record.id for record in await store.search("x", limit=5)] == [mine]


async def test_a_platform_malformed_id_is_not_found() -> None:
    fake = FakeMem0("platform")
    fake.answer = lambda request: (
        httpx2.Response(400, json={"error": "memory_id should be a valid UUID"}) if request.method == "GET" else None
    )
    async with _store(fake) as store:
        assert await _fails(store.delete("not-a-uuid")) == "record_not_found"


@pytest.mark.parametrize("api", ["platform", "oss"])
async def test_a_write_that_does_not_read_back_is_unconfirmed_and_not_retried(api: Api) -> None:
    fake = FakeMem0(api)
    fake.stored_text = lambda text: text.upper()
    async with _store(fake) as store:
        assert await _fails(store.add("likes tea")) == "write_unconfirmed"
        record_id = fake.row("likes tea")
        assert await _fails(store.update(record_id, "likes coffee")) == "write_unconfirmed"
    writes = [call for call in fake.calls() if call[0] in ("POST", "PUT")]
    assert len(writes) == 2


async def test_an_add_without_its_record_is_unconfirmed() -> None:
    fake = FakeMem0("platform")
    fake.answer = lambda request: (
        httpx2.Response(200, json={"event_id": "e1", "status": "PENDING"}) if request.method == "POST" else None
    )
    async with _store(fake) as store:
        assert await _fails(store.add("likes tea")) == "write_unconfirmed"
    assert fake.calls() == [("POST", "/v3/memories/add/")]


async def test_a_delete_that_leaves_the_record_is_unconfirmed() -> None:
    fake = FakeMem0("oss")
    record_id = fake.row("likes tea")
    fake.answer = lambda request: (
        httpx2.Response(200, json={"message": "Memory deleted successfully!"}) if request.method == "DELETE" else None
    )
    async with _store(fake) as store:
        assert await _fails(store.delete(record_id)) == "write_unconfirmed"


async def test_platform_pages_by_offset() -> None:
    fake = FakeMem0("platform")
    ids = [fake.row(f"fact {number}") for number in range(5)]
    async with _store(fake) as store:
        first = await store.list(limit=2)
        second = await store.list(limit=2, cursor=first.next_cursor)
        # A changed limit continues from the same offset without skipping or repeating.
        rest = await store.list(limit=3, cursor=second.next_cursor)
        assert await _fails(store.list(limit=2, cursor="page-2")) == "invalid_cursor"
    assert [record.id for record in first.records] == ids[:2] and first.next_cursor == "2"
    assert [record.id for record in second.records] == ids[2:4] and second.next_cursor == "4"
    assert [record.id for record in rest.records] == ids[4:] and rest.next_cursor is None
    assert [dict(request.url.params) for request in fake.requests[:3]] == [
        {"page": "1", "page_size": "2"},
        {"page": "2", "page_size": "2"},
        {"page": "2", "page_size": "3"},
    ]


async def test_self_hosted_pages_within_its_listing() -> None:
    fake = FakeMem0("oss")
    ids = [fake.row(f"fact {number}") for number in range(5)]
    async with _store(fake) as store:
        first = await store.list(limit=2)
        second = await store.list(limit=2, cursor=first.next_cursor)
        third = await store.list(limit=2, cursor=second.next_cursor)
    assert [record.id for record in first.records] == ids[:2] and first.next_cursor == "2"
    assert [record.id for record in second.records] == ids[2:4] and second.next_cursor == "4"
    assert [record.id for record in third.records] == ids[4:] and third.next_cursor is None
    assert [request.url.params["top_k"] for request in fake.requests] == ["3", "5", "7"]


@pytest.mark.parametrize("api", ["platform", "oss"])
@pytest.mark.parametrize("text", ["", "   ", "x" * 8001])
async def test_record_text_is_bounded_before_any_request(api: Api, text: str) -> None:
    fake = FakeMem0(api)
    record_id = fake.row("likes tea")
    async with _store(fake) as store:
        assert await _fails(store.add(text)) == "invalid_text"
        assert await _fails(store.update(record_id, text)) == "invalid_text"
    assert fake.requests == []


@pytest.mark.parametrize(
    ("status", "read", "write"),
    [
        (401, "unavailable", "unavailable"),
        (429, "unavailable", "unavailable"),
        (503, "unavailable", "write_unconfirmed"),
    ],
)
async def test_failing_answers_are_unavailable_and_leave_writes_unconfirmed(status: int, read: str, write: str) -> None:
    fake = FakeMem0("platform")
    record_id = fake.row("likes tea")
    fake.answer = lambda request: httpx2.Response(status, json={"error": "no"}) if request.method != "GET" else None
    async with _store(fake) as store:
        assert await _fails(store.search("x", limit=1)) == read
        assert await _fails(store.list(limit=1)) == read
        assert await _fails(store.purge()) == read
        assert await _fails(store.add("likes coffee")) == write
        assert await _fails(store.update(record_id, "likes coffee")) == write


async def test_transport_failures_and_oversized_or_invalid_bodies_are_unavailable() -> None:
    fake = FakeMem0("oss")

    def broken(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/search"):
            raise httpx2.ConnectError("refused", request=request)
        if request.method == "GET" and request.url.path.endswith("/memories"):
            return httpx2.Response(200, content=b"x" * (8 * 1024 * 1024 + 1))
        if request.method == "POST":
            raise httpx2.ReadTimeout("slow", request=request)
        return httpx2.Response(200, content=b"not json")

    fake.answer = broken
    async with _store(fake) as store:
        assert await _fails(store.search("x", limit=1)) == "unavailable"
        assert await _fails(store.list(limit=1)) == "unavailable"
        assert await _fails(store.purge()) == "unavailable"
        assert await _fails(store.add("likes tea")) == "write_unconfirmed"


@pytest.mark.parametrize("namespace", ["", "*", "alice bob", "tab\there", "x" * 257])
async def test_a_namespace_that_could_widen_the_filter_is_refused(namespace: str) -> None:
    with pytest.raises(ValueError, match="namespace"):
        async with _store(FakeMem0("oss"), namespace=namespace):
            pass


def test_definitions_declare_their_inputs() -> None:
    assert [definition.type for definition in BUILT_IN_MEMORY_PROVIDERS] == ["mem0_platform", "mem0_oss"]
    assert Mem0PlatformConfiguration().base_url == "https://api.mem0.ai"
    assert Mem0OSSConfiguration(base_url=" http://mem0:8000/ ").base_url == "http://mem0:8000"
    for invalid in ("ftp://mem0", "https://mem0.example/?key=1", "https://user:pw@mem0.example"):
        with pytest.raises(ValidationError):
            Mem0OSSConfiguration(base_url=invalid)
    with pytest.raises(ValueError, match="credential is required"):
        MEM0_PLATFORM.open({}, None, namespace=NAMESPACE)
    MEM0_OSS.open({"base_url": "http://mem0:8000"}, None, namespace=NAMESPACE)
