"""Key allocation and reference boundaries on both supported databases."""

import asyncio
import re
import secrets

import pytest
from a13n_service.application_errors import ApplicationError
from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.resource_keys import ResourceKey, insert_with_key, key_candidates
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .conftest import accept


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Code Reviewer", "code-reviewer"),
        ("  Foo / BAR _ baz  ", "foo-bar-baz"),
        ("中文 Agent 测试", "agent"),
        ("A" * 80, "a" * 64),
    ],
)
def test_readable_key(name, expected):
    assert next(key_candidates(name, "agent")) == expected


@pytest.mark.parametrize("name", ["中文", "🚀", "", "settings"])
def test_empty_and_reserved_names_get_hex_suffix(name):
    candidates = list(key_candidates(name, "agent"))
    assert len(candidates) == 8
    assert all(re.fullmatch(r"(?:agent|settings)-[0-9a-f]{4}", key) for key in candidates)


@pytest.mark.parametrize("key", ["UPPER", "a_b", "中文", "a--b", "-a", "a-", "a" * 65, "new", "settings", "api"])
def test_manual_key_validation(key):
    with pytest.raises(ValidationError):
        TypeAdapter(ResourceKey).validate_python(key)


@pytest.mark.anyio
async def test_concurrent_generated_keys_and_atomic_conflicts(identity_runtime, monkeypatch):
    sessions = identity_runtime.membership._sessions
    now = utc_now()

    async def create(requested=None):
        row = OrganizationRecord(id=new_object_id("org"), name="Team", created_at=now, updated_at=now)
        async with transaction(sessions) as session:
            await insert_with_key(session, row, prefix="org", requested=requested)
        return row.key

    keys = await asyncio.gather(*(create() for _ in range(6)))
    assert len(set(keys)) == 6
    assert "team" in keys
    assert all(key == "team" or re.fullmatch(r"team-[0-9a-f]{4}", key) for key in keys)
    with pytest.raises(ApplicationError, match="already in use") as conflict:
        await create("team")
    assert conflict.value.code == "resource_key_conflict"

    token_hex = secrets.token_hex
    monkeypatch.setattr(
        "a13n_service.resource_keys.secrets.token_hex", lambda size: "abcd" if size == 2 else token_hex(size)
    )
    await create("team-abcd")
    with pytest.raises(ApplicationError) as exhausted:
        await create()
    assert exhausted.value.code == "resource_key_exhausted"
    async with transaction(sessions) as session:
        assert len(list(await session.scalars(select(OrganizationRecord)))) == 7
        invalid = WorkspaceRecord(
            id=new_object_id("ws"),
            organization_id="org_missing",
            name="Missing parent",
            created_at=now,
            updated_at=now,
        )
        with pytest.raises(IntegrityError):
            await insert_with_key(session, invalid, prefix="workspace")


@pytest.mark.anyio
async def test_workspace_and_organization_keys_rename_without_aliases(identity_http):
    client, _runtime, issued = identity_http
    organization_id, workspace_id = await accept(client, issued)
    organization = (await client.get(f"/api/v1/organizations/{organization_id}")).json()
    workspace = await client.get(f"/api/v1/workspaces/{workspace_id}")
    old_key = workspace.json()["key"]
    by_key = await client.get(f"/api/v1/workspaces/{old_key}")
    assert by_key.json() == workspace.json()
    renamed = await client.patch(
        f"/api/v1/workspaces/{old_key}", headers={"If-Match": workspace.headers["ETag"]}, json={"key": "research"}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["id"] == workspace_id
    assert renamed.json()["name"] == workspace.json()["name"]
    assert (await client.get(f"/api/v1/workspaces/{old_key}")).status_code == 404
    assert (await client.get(f"/api/v1/workspaces/{workspace_id}")).json()["key"] == "research"

    # Display names repeat; generated keys remain independent from later name edits.
    created = await client.post(f"/api/v1/organizations/{organization['key']}/workspaces", json={"name": "Default"})
    assert created.status_code == 201, created.text
    assert created.json()["key"] == "default"
    conflict = await client.patch(
        f"/api/v1/workspaces/{created.json()['id']}",
        headers={"If-Match": created.headers["ETag"]},
        json={"key": "research"},
    )
    assert conflict.status_code == 409
    updated = await client.patch(
        "/api/v1/workspaces/research",
        headers={"If-Match": renamed.headers["ETag"]},
        json={"name": "Different display label"},
    )
    assert updated.json()["key"] == "research"

    org = await client.get(f"/api/v1/organizations/{organization['key']}")
    renamed_org = await client.patch(
        f"/api/v1/organizations/{organization['key']}", headers={"If-Match": org.headers["ETag"]}, json={"key": "acme"}
    )
    assert renamed_org.status_code == 200, renamed_org.text
    assert (await client.get(f"/api/v1/organizations/{organization['key']}")).status_code == 404
    assert (await client.get("/api/v1/organizations/acme")).json()["id"] == organization_id
    assert (await client.get("/api/v1/organizations/by-key/acme")).status_code == 404


@pytest.mark.anyio
async def test_api_key_context_and_cross_workspace_references(identity_http):
    client, _runtime, issued = identity_http
    organization_id, workspace_id = await accept(client, issued)
    other = (await client.post(f"/api/v1/organizations/{organization_id}/workspaces", json={"name": "Other"})).json()
    key = (await client.post(f"/api/v1/workspaces/{workspace_id}/personal-api-keys", json={"name": "SDK"})).json()
    await client.post("/api/v1/auth/logout")
    client.headers["Authorization"] = f"Bearer {key['bearer']}"
    context = await client.get("/api/v1/auth/context")
    assert context.status_code == 200
    assert context.json()["workspace_id"] == workspace_id
    assert (await client.get("/api/v1/workspaces/default")).status_code == 200
    for reference in (other["id"], other["key"]):
        assert (await client.get(f"/api/v1/workspaces/{reference}")).status_code == 404
    assert (
        await client.get("/api/v1/workspaces/default", headers={"X-A13N-Workspace-ID": other["id"]})
    ).status_code == 401
