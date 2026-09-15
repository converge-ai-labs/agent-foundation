"""Opt-in real OSS + PGVector tests; the default image uses deterministic embeddings.

TEST_MEM0_OSS_URL and TEST_MEM0_OSS_API_KEY select a disposable test backend.
No Platform key or real LLM is required. Only records created here are deleted.
"""

import asyncio
import os
from uuid import uuid4

import httpx2
import pytest
from a13n_harness.capabilities.mem0_backends import open_mem0_oss
from a13n_harness.memory import MemoryPaginationUnsupported, MemoryScope, MemorySubject
from a13n_service.app import Components, create_app
from a13n_service.memory.scopes import memory_subject

from ..models.conftest import ORG_ID, USER_ID, WORKSPACE_ID, actor
from ..models.test_router import settings

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not os.getenv("TEST_MEM0_OSS_URL"), reason="requires disposable OSS backend"),
]


async def test_service_real_unmodified_oss_crud_search_and_bounded_list(memory_sessions, service_database, tmp_path):
    async def authenticate(_request):
        return actor()

    app = create_app(settings(tmp_path, service_database), components=Components(request_authenticator=authenticate))
    async with open_mem0_oss(
        base_url=os.environ["TEST_MEM0_OSS_URL"], api_key=os.environ["TEST_MEM0_OSS_API_KEY"]
    ) as backend:
        schema = (await backend.client.get("openapi.json")).json()
        assert "/memories" in schema["paths"] and "/memories/page" not in schema["paths"]
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                provider = await client.post(
                    f"/api/v1/workspaces/{WORKSPACE_ID}/memory-providers",
                    json={
                        "type": "a13n.mem0-oss",
                        "name": "Live OSS",
                        "configuration": {"base_url": os.environ["TEST_MEM0_OSS_URL"]},
                        "credential": {"api_key": os.environ["TEST_MEM0_OSS_API_KEY"]},
                    },
                )
                assert provider.status_code == 201, provider.text
                provider_id = provider.json()["id"]
                path = f"/api/v1/workspaces/{WORKSPACE_ID}/memory-providers/{provider_id}/memories"
                params = {"scope": "user"}
                text = f"  Exact OSS memory {uuid4()}  "
                created = await client.post(path, params=params, json={"text": text})
                assert created.status_code == 201, created.text
                memory_id = created.json()["id"]
                try:
                    assert created.json()["memory"] == text
                    found = await client.post(path + "/search", params=params, json={"query": text})
                    assert found.status_code == 200, found.text
                    assert memory_id in {row["id"] for row in found.json()["items"]}
                    page = await client.get(path, params=params)
                    assert page.status_code == 200, page.text
                    assert page.json()["pagination"] is None
                    assert memory_id in {row["id"] for row in page.json()["items"]}
                    changed = await client.put(path + "/" + memory_id, params=params, json={"text": "updated exactly"})
                    assert changed.status_code == 200, changed.text
                    assert changed.json()["memory"] == "updated exactly"
                    foreign = MemorySubject(MemoryScope.USER, "foreign-" + str(uuid4()))
                    foreign_id = (await backend.add("foreign memory", subject=foreign)).id
                    try:
                        for method in ("GET", "PUT", "DELETE"):
                            kwargs = {"json": {"text": "forged"}} if method == "PUT" else {}
                            response = await client.request(method, path + "/" + foreign_id, params=params, **kwargs)
                            assert response.status_code == 404, response.text
                        assert (await backend.get(foreign_id, subject=foreign)).text == "foreign memory"
                    finally:
                        await backend.delete(foreign_id, subject=foreign)
                    subject = memory_subject(ORG_ID, WORKSPACE_ID, provider_id, MemoryScope.USER, USER_ID)
                    assert subject in (await backend.get(memory_id, subject=subject)).subjects
                finally:
                    deleted = await client.delete(path + "/" + memory_id, params=params)
                    assert deleted.status_code == 204, deleted.text
                assert (await client.get(path + "/" + memory_id, params=params)).status_code == 404


async def test_real_oss_bounds_1005_records_without_inventing_pagination():
    subject = MemorySubject(MemoryScope.THREAD, "pagination-" + str(uuid4()))
    expired = MemorySubject(MemoryScope.THREAD, "expired-" + str(uuid4()))
    ids = []
    async with open_mem0_oss(
        base_url=os.environ["TEST_MEM0_OSS_URL"], api_key=os.environ["TEST_MEM0_OSS_API_KEY"], timeout=60
    ) as backend:
        semaphore = asyncio.Semaphore(8)

        async def create(index):
            async with semaphore:
                ids.append((await backend.add(f"pagination record {index}", subject=subject)).id)

        async def delete(memory_id):
            async with semaphore:
                # Expired records are intentionally unavailable to scoped reads.
                # Cleanup uses only public DELETE and the IDs created by this test.
                response = await backend.client.delete(f"memories/{memory_id}")
                assert response.status_code in {200, 204, 404}

        try:
            # Real native adds, embeddings, PGVector and history: no Service mirror or seeded fake list.
            async with asyncio.TaskGroup() as group:
                for index in range(1005):
                    group.create_task(create(index))
            result = await backend.list(subject, limit=1000)
            seen = {item.id for item in result.items}
            assert len(seen) == 1000 and seen < set(ids)
            assert result.pagination is None
            # Records outside the loaded subset still exist and can be read by ID.
            omitted = next(iter(set(ids) - seen))
            assert (await backend.get(omitted, subject=subject)).id == omitted
            with pytest.raises(MemoryPaginationUnsupported):
                await backend.list(subject, limit=1000, cursor="not-a-real-page")
            for index in range(3):
                response = await backend.client.post(
                    "memories",
                    json={
                        "messages": [{"role": "user", "content": f"expired {index}"}],
                        "run_id": expired.value,
                        "infer": False,
                        "expiration_date": "2000-01-01",
                    },
                )
                response.raise_for_status()
                ids.append(response.json()["results"][0]["id"])
            result = await backend.list(expired, limit=1000)
            assert result.items == () and result.pagination is None
        finally:
            async with asyncio.TaskGroup() as group:
                for memory_id in ids:
                    group.create_task(delete(memory_id))
