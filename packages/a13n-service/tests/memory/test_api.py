import json
from contextlib import AsyncExitStack
from dataclasses import replace
from uuid import uuid4

import httpx
import httpx2
import pytest
from a13n_harness.capabilities.mem0 import Mem0Scope
from a13n_harness.capabilities.mem0_backends import Mem0OSSBackend, open_mem0_platform
from a13n_service.app import Components, create_app
from a13n_service.iam import AuthorizationError, PrincipalRef
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.memory.domain import MemoryScope
from a13n_service.memory.scopes import MemoryAuthorizer, memory_subject
from a13n_service.storage import transaction

from ..models.conftest import ORG_ID, USER_ID, WORKSPACE_ID, actor
from ..models.test_router import settings

pytestmark = pytest.mark.anyio


def native_transport(records, calls, response_type):
    """In-memory remote fixture; actual SDK/OSS requests still cross their transport boundary."""

    def respond(request):
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        calls.append((request.method, path, body))
        platform = path.startswith(("/v1/", "/v3/"))
        if request.method == "POST" and path in {"/memories", "/v3/memories/add/"}:
            assert body["infer"] is False
            scope = (
                body["filters"]
                if platform
                else {key: body[key] for key in ("run_id", "agent_id", "user_id") if key in body}
            )
            assert len(scope) == 1
            memory_id = str(uuid4())
            records[memory_id] = {"id": memory_id, "memory": body["messages"][0]["content"], **scope}
            return response_type(
                200, json={"results": [{"id": memory_id, "event": "ADD", "memory": records[memory_id]["memory"]}]}
            )
        if path in {"/memories", "/v3/memories/", "/search", "/v3/memories/search/"}:
            scope = body.get("filters") or {
                key: request.url.params[key] for key in ("run_id", "agent_id", "user_id") if key in request.url.params
            }
            selected = [dict(value) for value in records.values() if all(value.get(k) == v for k, v in scope.items())]
            limit = int(request.url.params.get("page_size", request.url.params.get("top_k", body.get("top_k", 20))))
            search = "search" in path
            if search:
                for value in selected:
                    value["score"] = 0.9
            offset = (int(request.url.params.get("page", "1")) - 1) * limit if platform else 0
            more = offset + limit < len(selected) and not search
            result = {"results": selected[offset : offset + limit]}
            if platform:
                result["next"] = "https://not-followed.invalid/" if more else None
            return response_type(200, json=result)
        memory_id = path.rstrip("/").split("/")[-1]
        if memory_id not in records:
            return response_type(404, json={"detail": "not found"})
        if request.method == "GET":
            return response_type(200, json=records[memory_id])
        if request.method == "PUT":
            records[memory_id]["memory"] = body["text"]
        elif request.method == "DELETE":
            del records[memory_id]
        else:
            raise AssertionError((request.method, path))
        return response_type(200, json={"message": "success"})

    return respond


@pytest.mark.parametrize("provider", ["oss", "platform"])
async def test_full_native_api_lifecycle_and_scope_isolation(
    memory_sessions, service_sqlite_database, tmp_path, monkeypatch, provider
):
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    records, calls = {}, []
    stack = AsyncExitStack()
    if provider == "oss":
        remote = httpx2.AsyncClient(
            base_url="http://mem0/", transport=httpx2.MockTransport(native_transport(records, calls, httpx2.Response))
        )
        backend = Mem0OSSBackend(remote)
    else:
        backend = await stack.enter_async_context(
            open_mem0_platform(api_key="test-platform-key", base_url="http://mem0")
        )
        sdk = backend.client
        await sdk.async_client.aclose()
        remote = httpx.AsyncClient(
            base_url="http://mem0", transport=httpx.MockTransport(native_transport(records, calls, httpx.Response))
        )
        sdk.async_client = remote

    async def authenticate(_request):
        return actor()

    app = create_app(
        settings(tmp_path, service_sqlite_database), components=Components(request_authenticator=authenticate)
    )
    async with stack, remote, app.router.lifespan_context(app):
        app.state.runtime.shared.memories.backend = backend
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as client:
            path = f"/api/v1/workspaces/{WORKSPACE_ID}/memories"
            params = {"scope": "user"}
            created = await client.post(path, params=params, json={"text": "  Exact text, not extracted.  "})
            assert created.status_code == 201, created.text
            memory_id = created.json()["id"]
            assert created.json()["memory"] == "  Exact text, not extracted.  "
            await client.post(path, params=params, json={"text": "Second memory"})
            page = await client.get(path, params={**params, "limit": 1})
            assert page.status_code == 200, page.text
            assert len(page.json()["items"]) == 1
            if provider == "platform":
                cursor = page.json()["pagination"]["next_cursor"]
                assert cursor
                next_page = await client.get(path, params={**params, "limit": 1, "cursor": cursor})
                assert next_page.status_code == 200, next_page.text
                assert len(next_page.json()["items"]) == 1
                assert next_page.json()["pagination"] == {"next_cursor": None}
                assert page.json()["items"][0]["id"] != next_page.json()["items"][0]["id"]
                assert (await client.get(path, params={**params, "limit": 2, "cursor": cursor})).status_code == 400
            else:
                assert page.json()["pagination"] is None
                # No cursor, total count, or claim that this one-record subset is exhaustive.
                assert set(page.json()) == {"items", "pagination"}
                assert (await client.get(path, params=params)).json()["pagination"] is None
                assert (await client.get(path, params={**params, "cursor": "invented"})).status_code == 400
            search = await client.post(path + "/search", params=params, json={"query": "text"})
            assert search.status_code == 200 and len(search.json()["items"]) == 2, search.text
            assert (await client.get(path + "/" + memory_id, params=params)).json()["memory"] == created.json()[
                "memory"
            ]
            changed = await client.put(path + "/" + memory_id, params=params, json={"text": "Updated"})
            assert changed.status_code == 200 and changed.json()["memory"] == "Updated", changed.text
            assert (await client.delete(path + "/" + memory_id, params=params)).status_code == 204
            assert (await client.get(path + "/" + memory_id, params=params)).status_code == 404
            before = len(calls)
            assert (
                await client.get(path.replace(WORKSPACE_ID, "ws_other1234567890"), params=params)
            ).status_code == 404
            assert (
                await client.post(path, params={**params, "subject_id": USER_ID}, json={"text": "forged"})
            ).status_code == 400
            assert len(calls) == before
            foreign = str(uuid4())
            records[foreign] = {"id": foreign, "memory": "another workspace", "user_id": "foreign"}
            for method in ("get", "put", "delete"):
                kwargs = {"json": {"text": "attack"}} if method == "put" else {}
                response = await client.request(method, path + "/" + foreign, params=params, **kwargs)
                assert response.status_code == 404, response.text
            assert records[foreign]["memory"] == "another workspace"
            async with transaction(memory_sessions) as session:
                binding = await session.get(RoleBindingRecord, "rb_ws1234567890abcde")
                binding.role_key = "viewer"
            before = len(calls)
            assert (await client.post(path, params=params, json={"text": "denied"})).status_code == 404
            assert len(calls) == before


async def test_service_account_never_becomes_user(memory_sessions):
    principal = PrincipalRef(principal_type="service_account", principal_id="sa_1234567890abcdef")
    with pytest.raises(AuthorizationError):
        await MemoryAuthorizer(memory_sessions).authorize(
            actor=replace(actor(), principal=principal), workspace_id=WORKSPACE_ID, selection=MemoryScope(scope="user")
        )


def test_namespace_covers_organization_workspace_subject_and_kind():
    base = memory_subject(ORG_ID, WORKSPACE_ID, Mem0Scope.USER, USER_ID)
    assert base != memory_subject(ORG_ID, "other", Mem0Scope.USER, USER_ID)
    assert base != memory_subject("other", WORKSPACE_ID, Mem0Scope.USER, USER_ID)
    assert base != memory_subject(ORG_ID, WORKSPACE_ID, Mem0Scope.AGENT, USER_ID)
    assert base != memory_subject(ORG_ID, WORKSPACE_ID, Mem0Scope.USER, "other")


async def test_oss_default_loads_1000_for_client_paging_without_completeness_claim(
    memory_sessions, service_sqlite_database, tmp_path
):
    from a13n_service.collection_cursors import encode_collection_cursor

    subject = memory_subject(ORG_ID, WORKSPACE_ID, Mem0Scope.USER, USER_ID)
    records = {str(index): {"id": str(index), "memory": f"record {index}", **subject.filter()} for index in range(1005)}
    calls = []
    transport = native_transport(records, calls, httpx2.Response)

    def handle(request):
        assert request.method == "GET" and request.url.path == "/memories"
        assert request.url.params["top_k"] == "1000"
        assert "cursor" not in request.url.params
        return transport(request)

    async def authenticate(_request):
        return actor()

    app = create_app(
        settings(tmp_path, service_sqlite_database), components=Components(request_authenticator=authenticate)
    )
    async with httpx2.AsyncClient(base_url="http://mem0/", transport=httpx2.MockTransport(handle)) as remote:
        async with app.router.lifespan_context(app):
            app.state.runtime.shared.memories.backend = Mem0OSSBackend(remote)
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                path = f"/api/v1/workspaces/{WORKSPACE_ID}/memories"
                response = await client.get(path, params={"scope": "user"})
                assert response.status_code == 200, response.text
                assert len(response.json()["items"]) == 1000
                assert response.json()["pagination"] is None
                assert set(response.json()) == {"items", "pagination"}
                cursor = encode_collection_cursor(
                    {"cursor": "invented"},
                    scope={"subject": subject.filter(), "limit": 1000, "backend": "Mem0OSSBackend"},
                    kind="memories",
                )
                rejected = await client.get(path, params={"scope": "user", "cursor": cursor})
                assert rejected.status_code == 400
                assert rejected.json()["error"]["code"] == "memory_pagination_unsupported"
                assert len(calls) == 1
