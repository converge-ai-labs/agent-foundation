from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2
import pytest
from a13n_service.api import install_api_conventions
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.hooks.management import HookSubscriptionService
from a13n_service.hooks.router import router
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.interactions.conftest import WORKSPACE_ID

from .support import RUN_ID, SECRET_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret


@pytest.fixture
async def hook_api_client(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
    service_runtime_factory,
) -> AsyncIterator[httpx2.AsyncClient]:
    await seed_run_and_secret(hook_interaction_sessions)
    await seed_hook_actor_access(hook_interaction_sessions)
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(router)
    hook_subscriptions = HookSubscriptionService(
        hook_interaction_sessions,
        EndpointPolicy(),
    )

    async def authenticate(_request: Request):
        return hook_actor()

    app.state.runtime = service_runtime_factory(
        request_authenticator=authenticate,
        hook_subscriptions=hook_subscriptions,
    )
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.anyio
async def test_hook_management_http_lifecycle_and_preconditions(hook_api_client: httpx2.AsyncClient) -> None:
    body = {
        "hook_names": ["run.accepted"],
        "run_id": RUN_ID,
        "webhook": {
            "endpoint_url": "https://93.184.216.34/hooks",
            "signing_secret_id": SECRET_ID,
        },
    }
    created = await hook_api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/hook-subscriptions",
        json=body,
    )
    assert created.status_code == 201
    subscription_id = created.json()["id"]
    etag = created.headers["ETag"]

    fetched = await hook_api_client.get(f"/api/v1/hook-subscriptions/{subscription_id}")
    listed = await hook_api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/hook-subscriptions")
    assert fetched.status_code == listed.status_code == 200
    assert fetched.headers["ETag"] == etag
    assert listed.json()["items"] == [created.json()]

    missing_precondition = await hook_api_client.patch(
        f"/api/v1/hook-subscriptions/{subscription_id}",
        json={"enabled": False},
    )
    assert missing_precondition.status_code == 400
    assert missing_precondition.json()["error"]["code"] == "invalid_request"

    paused = await hook_api_client.patch(
        f"/api/v1/hook-subscriptions/{subscription_id}",
        headers={"If-Match": etag},
        json={"enabled": False},
    )
    assert paused.status_code == 200
    assert paused.json()["enabled"] is False
    assert paused.json()["version"] == 1

    deleted = await hook_api_client.delete(
        f"/api/v1/hook-subscriptions/{subscription_id}",
        headers={"If-Match": paused.headers["ETag"]},
    )
    assert deleted.status_code == 204
    missing = await hook_api_client.get(f"/api/v1/hook-subscriptions/{subscription_id}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "hook_subscription_not_found"
