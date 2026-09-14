from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx2
import pytest
from a13n_service.api import install_api_conventions
from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.router import router
from a13n_service.lifecycle.service import LifecycleEventService
from a13n_service.storage import transaction
from fastapi import FastAPI, Request
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
