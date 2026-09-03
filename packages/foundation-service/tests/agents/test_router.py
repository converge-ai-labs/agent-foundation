from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx2
import pytest
from a13n_service.agents.router import router
from a13n_service.agents.service import AgentService
from a13n_service.api import install_api_conventions
from fastapi import FastAPI, Request

from .conftest import WORKSPACE_ID, actor, agent_config


@pytest.fixture
async def api_client(agent_service: AgentService) -> AsyncIterator[httpx2.AsyncClient]:
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(router)

    async def authenticate(_request: Request):
        return actor()

    app.state.runtime = SimpleNamespace(
        request_authenticator=authenticate,
        control=SimpleNamespace(agents=agent_service),
    )
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.anyio
async def test_complete_agent_http_lifecycle(api_client: httpx2.AsyncClient) -> None:
    created = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/agents",
        headers={"Idempotency-Key": "http-create"},
        json={"name": "HTTP Support", "config": agent_config().model_dump(mode="json", by_alias=True)},
    )
    assert created.status_code == 201
    result = created.json()
    agent = result["agent"]
    assert agent["version"] == result["revision"]["version"] == 1
    assert agent["current_revision_id"] == result["revision"]["id"]

    revision_result = await api_client.post(
        f"/api/v1/agents/{agent['id']}/revisions",
        headers={"Idempotency-Key": "http-create_revision"},
        json={
            "expected_version": 1,
            "config": agent_config(instructions="Changed").model_dump(mode="json", by_alias=True),
        },
    )
    assert revision_result.status_code == 201
    revised = revision_result.json()
    assert revised["revision"]["version"] == 2
    assert revised["agent"]["current_revision_id"] == revised["revision"]["id"]

    restored = await api_client.post(
        f"/api/v1/agents/{agent['id']}/revisions/{result['revision']['id']}/restore",
        headers={"Idempotency-Key": "http-restore"},
        json={"expected_version": 2},
    )
    assert restored.status_code == 201
    assert restored.json()["agent"]["version"] == 3
    assert restored.json()["revision"]["source_revision_id"] == result["revision"]["id"]

    listed = await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/agents")
    revisions = await api_client.get(f"/api/v1/agents/{agent['id']}/revisions")
    fetched_revision = await api_client.get(f"/api/v1/agent-revisions/{result['revision']['id']}")
    assert listed.status_code == revisions.status_code == fetched_revision.status_code == 200
    assert listed.json()["items"][0]["name"] == agent["name"]
    assert [item["version"] for item in revisions.json()["items"]] == [3, 2, 1]
    assert fetched_revision.json() == result["revision"]

    duplicated = await api_client.post(
        f"/api/v1/agents/{agent['id']}/duplicate",
        headers={"Idempotency-Key": "http-duplicate"},
        json={"expected_version": 3, "name": "HTTP Support Copy"},
    )
    assert duplicated.status_code == 201
    assert duplicated.json()["duplicated_from_revision_id"] == restored.json()["revision"]["id"]
    assert "agent" not in duplicated.json()


@pytest.mark.anyio
async def test_http_requires_idempotency_key_and_returns_safe_errors(api_client: httpx2.AsyncClient) -> None:
    missing_key = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/agents",
        json={"name": "Missing Key", "config": agent_config().model_dump(mode="json", by_alias=True)},
    )
    missing = await api_client.get("/api/v1/agents/ap_1234567890abcdef")

    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "invalid_request"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "agent_not_found"
