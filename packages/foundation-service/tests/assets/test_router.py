from __future__ import annotations

import asyncio
from base64 import b64encode
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import ServiceComponents, create_app
from a13n_service.assets.models import AssetRecord
from a13n_service.assets.objects import asset_content_key, asset_object_metadata
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord, OutboxRecord
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.domain import PrincipalRef
from a13n_service.iam.models import (
    OrganizationRecord,
    RoleBindingRecord,
    SecurityAuditRecord,
    UserRecord,
    WorkspaceRecord,
)
from a13n_service.settings import ServiceSettings
from a13n_service.storage import transaction
from a13n_service.storage.object_store import ObjectNotFound
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from fastapi import Request
from sqlalchemy import select

NOW = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
BUILDER_ID = "usr_1234567890abcdef"
VIEWER_ID = "usr_fedcba0987654321"
PDF = b"%PDF-1.7\nasset content\n%%EOF\n"


@dataclass(frozen=True, slots=True)
class Api:
    client: httpx2.AsyncClient
    app: object


async def authenticate(request: Request) -> AuthenticatedActor:
    principal_id = VIEWER_ID if request.headers.get("X-Test-Actor") == "viewer" else BUILDER_ID
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=principal_id),
        auth_method="session",
        credential_id="ses_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id=request.state.request_id,
    )


def settings(tmp_path: Path, database_path: Path) -> ServiceSettings:
    return ServiceSettings(
        _env_file=None,
        database_backend="sqlite",
        database_sqlite_path=database_path,
        redis_backend="memory",
        object_backend="local",
        object_local_root=tmp_path / "objects",
        filesystem_root=tmp_path / "files",
        asset_max_size_bytes=128,
        asset_cleanup_poll_interval_seconds=300,
        model_resolve_dns_on_save=False,
        secret_master_key_base64=b64encode(b"0123456789abcdef0123456789abcdef").decode(),
        secret_encryption_key_id="asset-management-test-key",
        connectivity_public_origin="http://testserver",
        connectivity_http_origins=("http://testserver",),
    )


async def seed_database(config: ServiceSettings) -> None:
    engine = create_sql_engine(config.database_config())
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, name="Test", created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                normalized_name="default",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        session.add_all(
            [
                UserRecord(
                    id=user_id,
                    email=f"{name}@example.com",
                    normalized_email=f"{name}@example.com",
                    name=name.title(),
                    status="active",
                    email_verified_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
                for user_id, name in ((BUILDER_ID, "builder"), (VIEWER_ID, "viewer"))
            ]
        )
        await session.flush()
        bindings: list[RoleBindingRecord] = []
        for suffix, user_id, role in (("build", BUILDER_ID, "builder"), ("viewr", VIEWER_ID, "viewer")):
            bindings.extend(
                (
                    RoleBindingRecord(
                        id=f"rb_org_{suffix}1234567890a",
                        organization_id=ORG_ID,
                        workspace_id=None,
                        principal_type="user",
                        principal_id=user_id,
                        resource_type="organization",
                        resource_id=ORG_ID,
                        role_key="member",
                        created_by_user_id=BUILDER_ID,
                        created_at=NOW,
                        updated_at=NOW,
                    ),
                    RoleBindingRecord(
                        id=f"rb_ws_{suffix}1234567890ab",
                        organization_id=ORG_ID,
                        workspace_id=WORKSPACE_ID,
                        principal_type="user",
                        principal_id=user_id,
                        resource_type="workspace",
                        resource_id=WORKSPACE_ID,
                        role_key=role,
                        created_by_user_id=BUILDER_ID,
                        created_at=NOW,
                        updated_at=NOW,
                    ),
                )
            )
        session.add_all(bindings)
    await engine.dispose()


@pytest.fixture
async def api(
    tmp_path: Path,
    service_sqlite_database: Path,
) -> AsyncIterator[Api]:
    config = settings(tmp_path, service_sqlite_database)
    await seed_database(config)
    app = create_app(config, components=ServiceComponents(request_authenticator=authenticate))
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield Api(client=client, app=app)


async def upload(
    client: httpx2.AsyncClient,
    *,
    key: str,
    content: bytes = PDF,
    filename: str = "résumé.pdf",
    media_type: str = "application/pdf",
    actor: str | None = None,
) -> httpx2.Response:
    headers = {"Content-Type": "application/octet-stream", "Idempotency-Key": key}
    if actor is not None:
        headers["X-Test-Actor"] = actor
    return await client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
        params={"filename": filename, "media_type": media_type},
        content=content,
        headers=headers,
    )


@pytest.mark.anyio
async def test_asset_http_lifecycle_idempotency_and_cleanup(api: Api) -> None:
    created_response = await upload(api.client, key="asset-create")
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["id"].startswith("ast_")
    assert created["filename"] == "résumé.pdf"
    assert created["media_type"] == "application/pdf"
    assert created["size_bytes"] == len(PDF)
    assert created["source"] == {
        "kind": "upload",
        "principal": {"principal_type": "user", "principal_id": BUILDER_ID},
    }

    replay = await upload(api.client, key="asset-create")
    assert replay.status_code == 201
    assert replay.json() == created
    conflict = await upload(api.client, key="asset-create", content=PDF + b"different")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "asset_idempotency_conflict"

    duplicate = await upload(api.client, key="asset-distinct")
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] != created["id"]
    listed = await api.client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/assets?source_kind=upload")
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["items"]} == {created["id"], duplicate.json()["id"]}
    run_filtered = await api.client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/assets?source_run_id=run_1234567890abcdef")
    assert run_filtered.json() == {"items": [], "next_cursor": None}

    assert (await api.client.get(f"/api/v1/assets/{created['id']}")).json() == created
    content = await api.client.get(f"/api/v1/assets/{created['id']}/content")
    assert content.status_code == 200
    assert content.content == PDF
    assert content.headers["content-type"] == "application/octet-stream"
    assert content.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")
    assert content.headers["etag"] == f'"sha256:{created["content_sha256"]}"'

    deleted = await api.client.delete(f"/api/v1/assets/{created['id']}")
    assert deleted.status_code == 204
    assert (await api.client.get(f"/api/v1/assets/{created['id']}")).status_code == 404
    assert (await api.client.get(f"/api/v1/assets/{created['id']}/content")).status_code == 404
    assert (await api.client.delete(f"/api/v1/assets/{created['id']}")).status_code == 404

    sessions = api.app.state.db_session_factory
    async with transaction(sessions) as session:
        evidence = tuple((await session.scalars(select(IdempotencyEvidenceRecord))).all())
        outbox = await session.scalar(select(OutboxRecord).where(OutboxRecord.source_id == created["id"]))
        audits = tuple((await session.scalars(select(SecurityAuditRecord))).all())
    assert len(evidence) == 2
    assert outbox is not None and outbox.status == "pending"
    assert {(item.action, item.outcome) for item in audits} >= {
        ("asset.create", "success"),
        ("asset.delete", "success"),
    }
    assert all(set(item.details or {}) <= {"source_kind"} for item in audits)

    assert await api.app.state.asset_cleanup_reconciler.reconcile_once() == 1
    async with transaction(sessions) as session:
        outbox = await session.scalar(select(OutboxRecord).where(OutboxRecord.source_id == created["id"]))
    assert outbox is not None and outbox.status == "published"
    key = asset_content_key(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, asset_id=created["id"])
    with pytest.raises(ObjectNotFound):
        await api.app.state.storage.objects.stat(key)


@pytest.mark.anyio
async def test_concurrent_same_key_uploads_reconcile_one_asset_and_object(api: Api) -> None:
    first, second = await asyncio.gather(
        upload(api.client, key="concurrent-upload"),
        upload(api.client, key="concurrent-upload"),
    )

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    async with transaction(api.app.state.db_session_factory) as session:
        records = tuple((await session.scalars(select(AssetRecord))).all())
    assert [record.id for record in records] == [first.json()["id"]]
    objects = await api.app.state.storage.objects.list(
        prefix=f"tenants/{ORG_ID}/workspaces/{WORKSPACE_ID}/assets/version-1/"
    )
    assert [item.key for item in objects.items] == [
        asset_content_key(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            asset_id=first.json()["id"],
        )
    ]


@pytest.mark.anyio
async def test_asset_upload_rejects_unsafe_inputs_limits_and_denied_callers(api: Api) -> None:
    wrong_transfer = await api.client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/assets?filename=x.pdf",
        content=PDF,
        headers={"Content-Type": "multipart/form-data", "Idempotency-Key": "wrong-transfer"},
    )
    assert wrong_transfer.status_code == 400
    assert wrong_transfer.json()["error"]["code"] == "invalid_request"

    invalid_name = await upload(api.client, key="invalid-name", filename="../secret.pdf")
    assert invalid_name.status_code == 400
    assert invalid_name.json()["error"]["code"] == "asset_content_invalid"
    conflict = await upload(api.client, key="type-conflict", media_type="image/png")
    assert conflict.status_code == 400
    assert conflict.json()["error"]["code"] == "asset_media_type_invalid"
    oversize = await upload(api.client, key="oversize", content=b"x" * 129, media_type="application/octet-stream")
    assert oversize.status_code == 400
    assert oversize.json()["error"]["code"] == "asset_limit"

    denied = await upload(api.client, key="viewer-create", actor="viewer")
    assert denied.status_code == 404
    async with transaction(api.app.state.db_session_factory) as session:
        audit = await session.scalar(
            select(SecurityAuditRecord).where(
                SecurityAuditRecord.actor_id == VIEWER_ID,
                SecurityAuditRecord.action == "asset.create",
            )
        )
    assert audit is not None and audit.outcome == "failure"


@pytest.mark.anyio
async def test_content_integrity_failure_returns_safe_typed_error(api: Api) -> None:
    created = (await upload(api.client, key="integrity")).json()
    key = asset_content_key(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, asset_id=created["id"])
    metadata = asset_object_metadata(
        asset_id=created["id"],
        workspace_id=WORKSPACE_ID,
        size_bytes=len(PDF),
        content_sha256=created["content_sha256"],
    )
    await api.app.state.storage.objects.put(
        key,
        b"corrupt",
        content_type="application/octet-stream",
        metadata=metadata,
    )

    response = await api.client.get(f"/api/v1/assets/{created['id']}/content")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "asset_content_unavailable"
    assert "tenants/" not in response.text
