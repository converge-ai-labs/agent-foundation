from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import httpx2
import pytest
from a13n_service.agents.application import AgentManagement
from a13n_service.agents.router import router
from a13n_service.api import install_api_conventions
from a13n_service.storage.object_store import LocalObjectStore
from fastapi import FastAPI, Request
from PIL import Image

from .conftest import DIRECT_USER_ID, NOW, ORG_ID, USER_ID, WORKSPACE_ID, actor, agent_config


@pytest.fixture
async def api_client(
    agent_management: AgentManagement, agent_sessions, process_runtime_factory, tmp_path: Path
) -> AsyncIterator[httpx2.AsyncClient]:
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(router)

    async def authenticate(request: Request):
        current = actor(request.headers.get("X-Test-User", USER_ID))
        if request.headers.get("X-Test-Boundary") == "organization":
            return replace(current, boundary_workspace_id=None, boundary_organization_id=ORG_ID)
        return current

    app.state.runtime = process_runtime_factory(
        request_authenticator=authenticate,
        agents=agent_management,
        sessions=agent_sessions,
    )
    app.state.runtime.shared.storage.objects = await LocalObjectStore.create(tmp_path / "images")
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
        f"/api/v1/workspaces/{WORKSPACE_ID}/agents/{agent['id']}/revisions",
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
        f"/api/v1/workspaces/{WORKSPACE_ID}/agents/{agent['id']}/revisions/{result['revision']['id']}/restore",
        headers={"Idempotency-Key": "http-restore"},
        json={"expected_version": 2},
    )
    assert restored.status_code == 201
    assert restored.json()["agent"]["version"] == 3
    assert restored.json()["revision"]["source_revision_id"] == result["revision"]["id"]

    listed = await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/agents")
    revisions = await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/agents/{agent['id']}/revisions")
    fetched_revision = await api_client.get(f"/api/v1/agent-revisions/{result['revision']['id']}")
    assert listed.status_code == revisions.status_code == fetched_revision.status_code == 200
    assert listed.json()["items"][0]["name"] == agent["name"]
    assert [item["version"] for item in revisions.json()["items"]] == [3, 2, 1]
    assert fetched_revision.json() == result["revision"]

    duplicated = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/agents/{agent['id']}/duplicate",
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
    missing = await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/agents/ap_1234567890abcdef")

    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "invalid_request"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "agent_not_found"


@pytest.mark.anyio
async def test_agent_references_and_key_changes(api_client: httpx2.AsyncClient) -> None:
    created = await api_client.post(
        "/api/v1/workspaces/default/agents",
        headers={"Idempotency-Key": "key-create"},
        json={"name": "Code Reviewer", "config": agent_config().model_dump(mode="json", by_alias=True)},
    )
    assert created.status_code == 201, created.text
    agent = created.json()["agent"]
    for workspace in (WORKSPACE_ID, "default"):
        for reference in (agent["id"], agent["key"]):
            response = await api_client.get(f"/api/v1/workspaces/{workspace}/agents/{reference}")
            assert response.json() == agent
    renamed = await api_client.patch(
        "/api/v1/workspaces/default/agents/code-reviewer",
        headers={"If-Match": created.headers["ETag"]},
        json={"key": "reviewer"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["id"] == agent["id"]
    assert renamed.json()["name"] == "Code Reviewer"
    assert (await api_client.get("/api/v1/workspaces/default/agents/code-reviewer")).status_code == 404
    assert (await api_client.get(f"/api/v1/agents/{agent['id']}")).status_code == 404
    assert (await api_client.get(f"/api/v1/workspaces/missing/agents/{agent['id']}")).status_code == 404
    duplicate = await api_client.post(
        "/api/v1/workspaces/default/agents/reviewer/duplicate",
        headers={"Idempotency-Key": "key-duplicate"},
        json={"name": "Code Reviewer", "expected_version": agent["version"]},
    )
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["key"] == "code-reviewer"
    conflict = await api_client.patch(
        "/api/v1/workspaces/default/agents/code-reviewer",
        headers={"If-Match": duplicate.headers["ETag"]},
        json={"key": "reviewer"},
    )
    assert conflict.status_code == 409
    for invalid in ("New", "new", "a_b", "a--b"):
        rejected = await api_client.patch(
            "/api/v1/workspaces/default/agents/reviewer",
            headers={"If-Match": renamed.headers["ETag"]},
            json={"key": invalid},
        )
        assert rejected.status_code == 400


@pytest.mark.anyio
async def test_browser_agent_keys_preserve_grants_and_parent_scope(api_client, agent_sessions):
    from a13n_service.iam.models import WorkspaceRecord
    from a13n_service.storage import transaction

    created = await api_client.post(
        "/api/v1/workspaces/default/agents",
        headers={"Idempotency-Key": "scope-create"},
        json={"name": "Assistant", "config": agent_config().model_dump(mode="json", by_alias=True)},
    )
    assert created.status_code == 201
    agent = created.json()["agent"]
    api_client.headers["X-Test-Boundary"] = "organization"
    assert (await api_client.get("/api/v1/workspaces/default/agents/assistant")).status_code == 200
    async with transaction(agent_sessions) as session:
        session.add(
            WorkspaceRecord(
                id="ws_9999999999999999",
                organization_id=ORG_ID,
                key="other",
                name="Other",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    for parent in ("other", "ws_9999999999999999"):
        assert (await api_client.get(f"/api/v1/workspaces/{parent}/agents/{agent['id']}")).status_code == 404
    api_client.headers["X-Test-User"] = DIRECT_USER_ID
    for reference in (agent["id"], agent["key"]):
        assert (await api_client.get(f"/api/v1/workspaces/default/agents/{reference}")).status_code == 404


@pytest.mark.anyio
async def test_agent_avatar_upload_replace_remove_and_authorization(api_client: httpx2.AsyncClient) -> None:
    created = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/agents",
        headers={"Idempotency-Key": "avatar-agent"},
        json={"name": "Avatar Agent", "config": agent_config().model_dump(mode="json", by_alias=True)},
    )
    assert created.status_code == 201
    original = created.json()["agent"]
    path = f"/api/v1/workspaces/{WORKSPACE_ID}/agents/{original['key']}/avatar"
    image = BytesIO()
    Image.new("RGB", (640, 320), "purple").save(image, format="PNG")
    content = image.getvalue()
    headers = {"If-Match": created.headers["ETag"], "Content-Type": "image/png"}
    invalid = await api_client.put(path, headers=headers, content=b"not an image")
    assert invalid.status_code == 400
    denied = await api_client.put(path, headers={**headers, "X-Test-User": DIRECT_USER_ID}, content=content)
    assert denied.status_code in {403, 404}
    uploaded = await api_client.put(path, headers=headers, content=content)
    assert uploaded.status_code == 200, uploaded.text
    avatar = uploaded.json()
    assert avatar["image_url"] and "image_id" not in avatar
    assert avatar["version"] == original["version"]
    assert avatar["current_revision_id"] == original["current_revision_id"]
    assert uploaded.headers["ETag"] != created.headers["ETag"]
    read = await api_client.get(avatar["image_url"])
    assert read.status_code == 200
    assert read.headers["Content-Type"] == "image/webp"
    assert read.headers["Cache-Control"] == "private, no-store"
    with Image.open(BytesIO(read.content)) as normalized:
        assert normalized.size == (512, 256)
    assert (await api_client.get(avatar["image_url"], headers={"X-Test-User": DIRECT_USER_ID})).status_code in {
        403,
        404,
    }
    assert (await api_client.put(path, headers=headers, content=content)).status_code == 412
    headers["If-Match"] = uploaded.headers["ETag"]
    replaced = await api_client.put(path, headers=headers, content=content)
    assert replaced.status_code == 200
    assert replaced.json()["image_url"] != avatar["image_url"]
    assert (await api_client.get(avatar["image_url"])).status_code == 404
    listed = await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/agents")
    assert listed.json()["items"][0]["image_url"] == replaced.json()["image_url"]
    removed = await api_client.delete(path, headers={"If-Match": replaced.headers["ETag"]})
    assert removed.status_code == 200
    assert removed.json()["image_url"] is None
    assert removed.json()["version"] == original["version"]
    assert (await api_client.get(replaced.json()["image_url"])).status_code == 404


@pytest.mark.anyio
async def test_agent_labels_http_contract(api_client):
    from tests.labels_support import assert_labels_http_contract

    collection = f"/api/v1/workspaces/{WORKSPACE_ID}/agents"
    created = await api_client.post(
        collection,
        headers={"Idempotency-Key": "label-agent"},
        json={"name": "Label agent", "config": agent_config().model_dump(mode="json", by_alias=True)},
    )
    assert created.status_code == 201, created.text
    identity = created.json()["agent"]["id"]
    await assert_labels_http_contract(
        api_client, f"{collection}/{identity}", collection, immutable_fields=["version", "current_revision_id"]
    )
