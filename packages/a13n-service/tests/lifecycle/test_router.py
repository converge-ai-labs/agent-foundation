from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx2
import pytest
from a13n_service.api import install_api_conventions
from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.domain import LifecycleEventDraft
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.lifecycle.persistence import append_lifecycle_event
from a13n_service.lifecycle.router import router
from a13n_service.lifecycle.service import LifecycleEventService
from a13n_service.storage import transaction
from fastapi import FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.hooks.support import RUN_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret
from tests.interactions.conftest import NOW, USER_ID, WORKSPACE_ID
from tests.lifecycle_support import test_lifecycle_writer


@pytest.fixture
async def lifecycle_api_client(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    process_runtime_factory,
) -> AsyncIterator[httpx2.AsyncClient]:
    await seed_run_and_secret(lifecycle_interaction_sessions)
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with transaction(lifecycle_interaction_sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        assert run is not None
        await test_lifecycle_writer().append_run_lifecycle(
            database,
            run,
            "run.accepted",
            mutation_id="mut_9191919191919191",
            occurred_at=NOW + timedelta(seconds=1),
            actor_type="user",
            actor_id=USER_ID,
        )

    app = FastAPI()
    install_api_conventions(app)
    app.include_router(router)
    lifecycle_events = LifecycleEventService(lifecycle_interaction_sessions)

    async def authenticate(_request: Request):
        return hook_actor()

    app.state.runtime = process_runtime_factory(
        request_authenticator=authenticate,
        sessions=lifecycle_interaction_sessions,
        lifecycle_events=lifecycle_events,
    )
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.anyio
async def test_lifecycle_reconciliation_http_collections(lifecycle_api_client: httpx2.AsyncClient) -> None:
    workspace = await lifecycle_api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/events")
    run = await lifecycle_api_client.get(f"/api/v1/runs/{RUN_ID}/events")
    invalid = await lifecycle_api_client.get(f"/api/v1/runs/{RUN_ID}/events?after_resource_seq=-1")
    missing = await lifecycle_api_client.get("/api/v1/runs/run_9999999999999999/events")

    assert workspace.status_code == run.status_code == 200
    assert workspace.json()["items"][0]["event_type"] == "run.accepted"
    assert run.json()["resource_type"] == "run"
    assert run.json()["next_resource_seq"] == 1
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_request"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "resource_not_found"


@pytest.mark.anyio
async def test_event_http_reads_project_historical_internal_fields(
    lifecycle_api_client: httpx2.AsyncClient,
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Seed the durable shape written before public projections were introduced.
    async with transaction(lifecycle_interaction_sessions) as database:
        run = await database.get(RunRecord, RUN_ID, with_for_update=True)
        record = await append_lifecycle_event(
            database,
            LifecycleEventDraft(
                organization_id=run.organization_id,
                entity_type="run",
                entity_id=run.id,
                entity_version=run.version,
                event_type="run.completed",
                mutation_id="mut_1234567890abcdef",
                session_id=run.session_id,
                thread_id=run.thread_id,
                run_id=run.id,
                actor_type="worker",
                actor_id="wrk_private",
                occurred_at=NOW + timedelta(seconds=2),
                payload={"output_object": {"object_key": "private/output.json", "size_bytes": 256}},
            ),
        )
        record.projection_state = "projecting"
        record.projection_next_attempt_at = None
        record.projection_lease_owner = "wrk_projector"
        record.projection_lease_expires_at = NOW + timedelta(minutes=1)
    for path in (f"/api/v1/workspaces/{WORKSPACE_ID}/events", f"/api/v1/runs/{RUN_ID}/events"):
        response = await lifecycle_api_client.get(path)
        assert response.status_code == 200
        item = response.json()["items"][-1]
        assert item["actor_id"] is None
        assert item["payload"] == {"output_object": {"size_bytes": 256}}
        assert "projection_lease_owner" not in item
        assert item["projection_state"] == "projecting"
        assert item["run_id"] == RUN_ID
    async with transaction(lifecycle_interaction_sessions) as database:
        record = await database.scalar(
            select(LifecycleEventRecord).where(LifecycleEventRecord.event_type == "run.completed")
        )
        assert record.actor_id == "wrk_private"
        assert record.projection_lease_owner == "wrk_projector"
        assert record.payload["output_object"]["object_key"] == "private/output.json"
