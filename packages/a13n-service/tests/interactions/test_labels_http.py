"""Label metadata is independent of execution, scoped, and concurrency-safe."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import httpx2
import pytest
from a13n_service.api import install_api_conventions
from a13n_service.gateway.labels import InteractionLabels
from a13n_service.gateway.queries import NativeInteractionQueries
from a13n_service.gateway.router import router
from a13n_service.iam.models import RoleBindingRecord, SecurityAuditRecord
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.run_stream import RunReplayStore
from a13n_service.storage import transaction
from a13n_service.storage.object_store import LocalObjectStore
from fastapi import FastAPI, Request
from sqlalchemy import func, select

from tests.hooks.support import RUN_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret
from tests.interactions.conftest import SESSION_ID, THREAD_ID, WORKSPACE_ID

pytestmark = pytest.mark.anyio
RESOURCES = [("sessions", SESSION_ID, SessionRecord), ("threads", THREAD_ID, ThreadRecord), ("runs", RUN_ID, RunRecord)]


@pytest.fixture
async def label_client(interaction_sessions, process_runtime_factory, tmp_path):
    sessions = interaction_sessions
    await seed_run_and_secret(sessions)
    await seed_hook_actor_access(sessions)
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(router)

    async def authenticate(request: Request):
        return hook_actor()

    objects = await LocalObjectStore.create(tmp_path / "labels-objects")
    app.state.runtime = process_runtime_factory(
        request_authenticator=authenticate,
        sessions=sessions,
        gateway=SimpleNamespace(
            labels=InteractionLabels(sessions), queries=NativeInteractionQueries(sessions, RunReplayStore(objects))
        ),
    )
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.parametrize("collection,resource_id,model", RESOURCES)
async def test_label_http_replacement_preserves_execution_and_audits_once(
    label_client, interaction_sessions, collection, resource_id, model
):
    client, sessions = label_client, interaction_sessions
    path = f"/api/v1/{collection}/{resource_id}/labels"
    read = await client.get(path)
    assert read.status_code == 200, read.text
    before = {}
    async with transaction(sessions) as database:
        for table, identity in [(SessionRecord, SESSION_ID), (ThreadRecord, THREAD_ID), (RunRecord, RUN_ID)]:
            row = await database.get(table, identity)
            before[identity] = {column.key: getattr(row, column.key) for column in table.__table__.columns}
    put = await client.put(
        path, headers={"If-Match": read.headers["etag"]}, json={"labels": {"batch": "one", "empty": ""}}
    )
    assert put.status_code == 200, put.text
    assert put.headers["etag"] != read.headers["etag"]
    noop = await client.put(path, headers={"If-Match": put.headers["etag"]}, json=put.json())
    assert noop.status_code == 200
    async with transaction(sessions) as database:
        for table, identity in [(SessionRecord, SESSION_ID), (ThreadRecord, THREAD_ID), (RunRecord, RUN_ID)]:
            row = await database.get(table, identity)
            after = {column.key: getattr(row, column.key) for column in table.__table__.columns}
            if identity == resource_id:
                assert after.pop("labels") == {"batch": "one", "empty": ""}
                assert after.pop("updated_at") > before[identity]["updated_at"]
                expected = {
                    key: value for key, value in before[identity].items() if key not in {"labels", "updated_at"}
                }
            else:
                expected = before[identity]
            assert after == expected
        assert await database.scalar(select(func.count()).select_from(SecurityAuditRecord)) == 1
    stale = await client.put(path, headers={"If-Match": read.headers["etag"]}, json={"labels": {}})
    assert stale.status_code == 412, stale.text
    missing = await client.put(path, headers={"If-Match": put.headers["etag"]}, json={})
    assert missing.status_code == 400
    clear = await client.put(path, headers={"If-Match": put.headers["etag"]}, json={"labels": {}})
    assert clear.status_code == 200 and clear.json() == {"labels": {}}


async def test_runtime_update_does_not_stale_label_etag(label_client, interaction_sessions):
    path = f"/api/v1/runs/{RUN_ID}/labels"
    old = await label_client.get(path)
    async with transaction(interaction_sessions) as database:
        row = await database.get(RunRecord, RUN_ID)
        row.version += 1
        row.updated_at += timedelta(seconds=1)
    put = await label_client.put(path, headers={"If-Match": old.headers["etag"]}, json={"labels": {"batch": "one"}})
    assert put.status_code == 200, put.text


@pytest.mark.parametrize("role", ["viewer", "runner"])
async def test_read_role_cannot_mutate_any_interaction_labels(label_client, interaction_sessions, role):
    async with transaction(interaction_sessions) as database:
        binding = await database.get(RoleBindingRecord, "rb_hookws717171717")
        binding.role_key = role
    for collection, identity, _ in RESOURCES:
        path = f"/api/v1/{collection}/{identity}/labels"
        read = await label_client.get(path)
        assert read.status_code == 200, read.text
        put = await label_client.put(
            path, headers={"If-Match": read.headers["etag"]}, json={"labels": {"changed": "true"}}
        )
        assert put.status_code == 404, put.text


async def test_filter_uses_own_labels_and_rejects_conflicts(label_client):
    path = f"/api/v1/sessions/{SESSION_ID}/labels"
    read = await label_client.get(path)
    await label_client.put(path, headers={"If-Match": read.headers["etag"]}, json={"labels": {"project": "mine"}})
    sessions = await label_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/sessions", params={"label": "project=mine"})
    assert sessions.status_code == 200, sessions.text
    assert [item["id"] for item in sessions.json()["items"]] == [SESSION_ID]
    runs = await label_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/runs", params={"label": "project=mine"})
    assert runs.status_code == 200 and runs.json()["items"] == []
    invalid = await label_client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/runs", params=[("label", "project=a"), ("label", "project=b")]
    )
    assert invalid.status_code == 400, invalid.text


async def test_concurrent_postgres_edit_has_one_winner(label_client):
    path = f"/api/v1/runs/{RUN_ID}/labels"
    read = await label_client.get(path)
    responses = await asyncio.gather(
        *[
            label_client.put(path, headers={"If-Match": read.headers["etag"]}, json={"labels": {"winner": value}})
            for value in ["a", "b"]
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 412], [r.text for r in responses]
