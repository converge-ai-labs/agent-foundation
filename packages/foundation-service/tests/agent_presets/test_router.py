from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2
import pytest
from a13n_service.agent_presets.router import router
from a13n_service.agent_presets.service import AgentPresetService
from a13n_service.api import install_api_conventions
from fastapi import FastAPI, Request

from .conftest import WORKSPACE_ID, actor, preset_config


@pytest.fixture
async def api_client(agent_preset_service: AgentPresetService) -> AsyncIterator[httpx2.AsyncClient]:
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(router)
    app.state.agent_preset_service = agent_preset_service

    async def authenticate(_request: Request):
        return actor()

    app.state.request_authenticator = authenticate
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.anyio
async def test_complete_agent_preset_http_lifecycle(api_client: httpx2.AsyncClient) -> None:
    created = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/agent-presets",
        headers={"Idempotency-Key": "http-create"},
        json={"name": "HTTP Support", "config": preset_config().model_dump(mode="json", by_alias=True)},
    )
    assert created.status_code == 201
    preset = created.json()
    assert preset["config_changed_since_revision"]

    revision_result = await api_client.post(
        f"/api/v1/agent-presets/{preset['id']}/revisions",
        headers={"Idempotency-Key": "http-create_revision"},
        json={"expected_resource_version": 1},
    )
    assert revision_result.status_code == 201
    result = revision_result.json()
    assert result["revision"]["revision_number"] == 1
    assert result["preset"]["default_revision_id"] is None

    selected = await api_client.post(
        f"/api/v1/agent-presets/{preset['id']}/set-default-revision",
        headers={"Idempotency-Key": "http-set-default"},
        json={
            "expected_resource_version": result["preset"]["resource_version"],
            "revision_id": result["revision"]["id"],
        },
    )
    assert selected.status_code == 200
    assert selected.json()["default_revision_id"] == result["revision"]["id"]

    listed = await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/agent-presets")
    revisions = await api_client.get(f"/api/v1/agent-presets/{preset['id']}/revisions")
    fetched_revision = await api_client.get(f"/api/v1/agent-preset-revisions/{result['revision']['id']}")
    assert listed.status_code == revisions.status_code == fetched_revision.status_code == 200
    assert listed.json()["items"][0]["config"] == preset["config"]
    assert revisions.json()["items"] == [result["revision"]]
    assert fetched_revision.json() == result["revision"]

    duplicated = await api_client.post(
        f"/api/v1/agent-presets/{preset['id']}/duplicate",
        headers={"Idempotency-Key": "http-duplicate"},
        json={"expected_resource_version": selected.json()["resource_version"], "name": "HTTP Support Copy"},
    )
    assert duplicated.status_code == 201
    assert duplicated.json()["duplicated_from_revision_id"] == result["revision"]["id"]
    assert "preset" not in duplicated.json()


@pytest.mark.anyio
async def test_http_requires_idempotency_key_and_returns_safe_errors(api_client: httpx2.AsyncClient) -> None:
    missing_key = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/agent-presets",
        json={"name": "Missing Key", "config": preset_config().model_dump(mode="json", by_alias=True)},
    )
    missing = await api_client.get("/api/v1/agent-presets/ap_1234567890abcdef")

    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "invalid_request"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "preset_not_found"
