"""Upload staging and assets: tenant-bound handles, idempotent staging, retirement and preconditions."""

import asyncio

import pytest
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.assets.service import require_usable
from a13n_service.resources.assets.usage import get_workspace_storage
from a13n_service.resources.uploads import service as uploads
from a13n_service.resources.uploads.schemas import Upload
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal, WorkspaceScope
from a13n_service.tenancy.tables import PrincipalRow, WorkspaceRow
from sqlalchemy import select

pytestmark = pytest.mark.anyio


def etag(resource: dict) -> str:
    return f'"{resource["id"]}:{resource["version"]}"'


async def upload(service, content: bytes, key: str, filename: str = "notes.txt") -> dict:  # type: ignore[no-untyped-def]
    response = await service.client.post(
        f"{service.api}/uploads",
        files={"file": (filename, content, "text/plain")},
        headers={"idempotency-key": key},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_asset_lifecycle_from_upload_to_retirement(service) -> None:  # type: ignore[no-untyped-def]
    staged = await upload(service, b"hello", "upload-1")
    assert staged["size"] == 5 and staged["content_type"] == "text/plain"
    # The same request key and bytes name the same upload; different bytes under that key are refused.
    assert (await upload(service, b"hello", "upload-1"))["upload_id"] == staged["upload_id"]
    reused = await service.client.post(
        f"{service.api}/uploads",
        files={"file": ("notes.txt", b"other", "text/plain")},
        headers={"idempotency-key": "upload-1"},
    )
    assert reused.status_code == 409 and reused.json()["error"]["details"]["reason"] == "idempotency_key_reused"

    assets = f"{service.api}/assets"
    created = await service.client.post(assets, json={"upload_id": staged["upload_id"], "name": "notes.txt"})
    assert created.status_code == 201, created.text
    asset = created.json()
    assert created.headers["etag"] == etag(asset)
    assert (asset["name"], asset["size"], asset["digest"]) == ("notes.txt", 5, staged["digest"])
    again = await service.client.post(assets, json={"upload_id": staged["upload_id"], "name": "notes.txt"})
    assert again.status_code == 200 and again.json()["id"] == asset["id"]
    renamed = await service.client.post(assets, json={"upload_id": staged["upload_id"], "name": "other.txt"})
    assert renamed.status_code == 409

    listed = (await service.client.get(assets)).json()
    assert [item["id"] for item in listed["items"]] == [asset["id"]] and listed["next_cursor"] is None
    content = await service.client.get(f"{assets}/{asset['id']}/content")
    assert content.content == b"hello"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert content.headers["content-disposition"] == "attachment; filename*=UTF-8''notes.txt"

    item = f"{assets}/{asset['id']}"
    assert (await service.client.delete(item)).status_code == 428
    assert (await service.client.delete(item, headers={"if-match": '"stale:1"'})).status_code == 412
    retired = await service.client.delete(item, headers={"if-match": etag(asset)})
    assert retired.status_code == 200 and retired.json()["retired_at"] is not None
    # Retired content stays readable; new input may not name it.
    assert (await service.client.get(f"{item}/content")).content == b"hello"
    async with short_session(service.runtime.storage) as session:
        with pytest.raises(ServiceError) as refused:
            await require_usable(session, service.tenant.workspace_id, {"content.0.asset_id": asset["id"]})
        actions = set((await session.scalars(select(AuditEventRow.action))).all())
    assert refused.value.details == {
        "field": "content.0.asset_id",
        "reason": "not_usable",
        "kind": "asset",
        "id": asset["id"],
    }
    assert {"asset.create", "asset.retire"} <= actions


async def test_upload_bounds_and_workspace_binding(service) -> None:  # type: ignore[no-untyped-def]
    too_large = await service.client.post(
        f"{service.api}/uploads",
        files={"file": ("big.bin", b"x" * (service.runtime.settings.objects.upload_bytes + 1), "text/plain")},
        headers={"idempotency-key": "big"},
    )
    assert too_large.status_code == 413
    unkeyed = await service.client.post(f"{service.api}/uploads", files={"file": ("a.txt", b"a", "text/plain")})
    assert unkeyed.status_code == 400

    storage, objects = service.runtime.storage, service.runtime.objects
    organization_id = service.tenant.organization_id
    viewer = Principal(
        service.tenant.principal_id,
        "user",
        (Grant(organization_id, service.tenant.workspace_id, BUILT_IN_ROLES["viewer"]),),
    )
    with pytest.raises(ServiceError) as denied:
        await uploads.stage(
            storage,
            objects,
            viewer,
            service.tenant.workspace_id,
            request_key="viewer",
            filename="a.txt",
            content_type="text/plain",
            content=b"a",
        )
    assert denied.value.code == "forbidden"

    # An upload staged in another workspace cannot become an asset here.
    other = new_object_id("ws")
    async with transaction(storage) as session:
        session.add(WorkspaceRow(id=other, organization_id=organization_id, name="Other"))
    elsewhere = await service.client.post(
        f"{service.api}/uploads",
        files={"file": ("a.txt", b"a", "text/plain")},
        headers={"idempotency-key": "elsewhere", "x-workspace-id": other},
    )
    assert elsewhere.status_code == 200, elsewhere.text
    borrowed = await service.client.post(
        f"{service.api}/assets", json={"upload_id": elsewhere.json()["upload_id"], "name": "a.txt"}
    )
    assert borrowed.status_code == 404


async def test_workspace_storage_counts_tracked_bytes_once_by_creator(service) -> None:  # type: ignore[no-untyped-def]
    storage, objects = service.runtime.storage, service.runtime.objects
    tenant = service.tenant
    workspace = f"{service.api}/storage"
    response = await service.client.get(workspace)
    assert response.json() == dict(active_bytes=0, retired_bytes=0, staged_upload_bytes=0, total_bytes=0, by_creator=[])

    first = await upload(service, b"abc", "active")
    second = await upload(service, b"defgh", "retired")
    creator = new_object_id("usr")
    other = new_object_id("ws")
    async with transaction(storage) as session:
        session.add(PrincipalRow(id=creator, kind="user", name="Other creator", email="creator@example.test"))
        session.add(WorkspaceRow(id=other, organization_id=tenant.organization_id, name="Other"))
    await uploads.store(
        storage,
        objects,
        WorkspaceScope(tenant.organization_id, tenant.workspace_id),
        creator,
        request_key="staged",
        filename="unassigned.txt",
        content_type="text/plain",
        content=b"1234567",
    )
    await uploads.store(
        storage,
        objects,
        WorkspaceScope(tenant.organization_id, other),
        creator,
        request_key="other",
        filename="other.txt",
        content_type="text/plain",
        content=b"other",
    )

    before = (await service.client.get(workspace)).json()
    assert before["staged_upload_bytes"] == before["total_bytes"] == 15
    for staged, name in [(first, "active.txt"), (second, "retired.txt")]:
        created = await service.client.post(
            f"{service.api}/assets", json={"upload_id": staged["upload_id"], "name": name}
        )
        assert created.status_code == 201, created.text
        if name == "retired.txt":
            asset = created.json()
            retired = await service.client.delete(
                f"{service.api}/assets/{asset['id']}", headers={"if-match": etag(asset)}
            )
            assert retired.status_code == 200, retired.text
    response = await service.client.get(workspace)
    assert response.status_code == 200, response.text
    report = response.json()
    assert [report[key] for key in ("active_bytes", "retired_bytes", "staged_upload_bytes", "total_bytes")] == [
        3,
        5,
        7,
        15,
    ]
    by_creator = {row["created_by_id"]: row for row in report["by_creator"]}
    assert by_creator[tenant.principal_id]["total_bytes"] == 8
    assert by_creator[tenant.principal_id]["staged_upload_bytes"] == 0
    assert by_creator[creator] == dict(
        created_by_id=creator,
        creator_name="Other creator",
        active_bytes=0,
        retired_bytes=0,
        staged_upload_bytes=7,
        total_bytes=7,
    )

    viewer = Principal(creator, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["viewer"]),))
    assert (await get_workspace_storage(storage, viewer, tenant.workspace_id)).model_dump() == report
    with pytest.raises(ServiceError):
        await get_workspace_storage(storage, viewer, other)


async def test_asset_filters_cover_the_full_collection(service) -> None:  # type: ignore[no-untyped-def]
    assets = f"{service.api}/assets"
    for i, name in enumerate(["report-100%.txt", "report-100x.txt", "readme.txt"]):
        staged = await upload(service, str(i).encode(), f"upload-{i}", name)
        response = await service.client.post(assets, json={"upload_id": staged["upload_id"], "name": name})
        assert response.status_code == 201, response.text
        if "%" in name:
            asset = response.json()
            retired = await service.client.delete(f"{assets}/{asset['id']}", headers={"if-match": etag(asset)})
            assert retired.status_code == 200, retired.text
    params = {"query": "REPORT", "source_kind": "user_upload", "created_by_id": service.tenant.principal_id, "limit": 1}
    first = (await service.client.get(assets, params=params)).json()
    second = (await service.client.get(assets, params={**params, "cursor": first["next_cursor"]})).json()
    assert first["total_count"] == second["total_count"] == 2
    assert second["next_cursor"] is None
    changed = await service.client.get(assets, params={**params, "status": "active", "cursor": first["next_cursor"]})
    assert changed.status_code == 400 and changed.json()["error"]["code"] == "invalid_cursor"
    assert {first["items"][0]["name"], second["items"][0]["name"]} == {"report-100%.txt", "report-100x.txt"}
    literal_match = (await service.client.get(assets, params={**params, "query": "%", "status": "retired"})).json()
    assert [row["name"] for row in literal_match["items"]] == ["report-100%.txt"]
    for extra in ({"status": "active"}, {"created_by_id": new_object_id("usr")}, {"source_kind": "agent_run"}):
        result = (await service.client.get(assets, params={**params, "query": "%", **extra})).json()
        assert result == {"items": [], "total_count": 0, "next_cursor": None}


@pytest.mark.parametrize("other", [b"a", b"b"])
async def test_concurrent_staging_under_one_request_key(service, monkeypatch, other: bytes) -> None:  # type: ignore[no-untyped-def]
    """Both requests miss the lookup and store their bytes; the unique index keeps one upload, which the other
    returns for the same bytes and refuses for different ones."""
    storage, objects = service.runtime.storage, service.runtime.objects
    scope = WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id)
    original, both, writes = objects.put, asyncio.Event(), []

    async def put(key: str, data: bytes, *, content_type: str):  # type: ignore[no-untyped-def]
        writes.append(key)
        if len(writes) == 2:
            both.set()
        await both.wait()
        return await original(key, data, content_type=content_type)

    monkeypatch.setattr(objects, "put", put)

    async def stage(content: bytes) -> Upload:
        return await uploads.store(
            storage,
            objects,
            scope,
            service.tenant.principal_id,
            request_key="race",
            filename="a.txt",
            content_type="text/plain",
            content=content,
        )

    results = await asyncio.gather(stage(b"a"), stage(other), return_exceptions=True)
    staged = [result for result in results if not isinstance(result, BaseException)]
    if other == b"a":
        assert len(staged) == 2 and staged[0] == staged[1]
    else:
        (refused,) = [result for result in results if isinstance(result, ServiceError)]
        assert len(staged) == 1 and refused.details["reason"] == "idempotency_key_reused"
