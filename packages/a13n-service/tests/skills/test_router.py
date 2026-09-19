from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from base64 import b64encode
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import Components, create_app
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.domain import PrincipalRef
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.settings import Settings
from a13n_service.skills.router import _content_chunks
from a13n_service.storage import transaction
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from fastapi import Request

NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
BUILDER_ID = "usr_1234567890abcdef"


def archive(*, body: str = "# Workflow") -> bytes:
    document = (f"---\nname: deploy-helper\ndescription: Deploy a reviewed service.\n---\n\n{body}\n").encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as target:
        info = zipfile.ZipInfo("SKILL.md")
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        target.writestr(info, document)
    return output.getvalue()


async def authenticate(request: Request) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=BUILDER_ID),
        auth_method="session",
        credential_id="ses_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id=request.state.request_id,
    )


def settings(tmp_path: Path, database: PostgreSQLConfig) -> Settings:
    return Settings(
        database={"url": database.url.get_secret_value()},
        redis={"backend": "memory"},
        objects={"backend": "local", "local_root": tmp_path / "objects"},
        filesystem={"root": tmp_path / "files"},
        models={"resolve_dns_on_save": False},
        secrets={
            "master_key_base64": b64encode(b"0123456789abcdef0123456789abcdef").decode(),
            "encryption_key_id": "skill-management-test-key",
        },
        connectivity={"public_origin": "http://127.0.0.1", "http_origins": ("http://127.0.0.1",)},
    )


async def seed_database(config: Settings) -> None:
    engine = create_sql_engine(config.database_config())
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, key="test", name="Test", created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                key="default",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        session.add(
            UserRecord(
                id=BUILDER_ID,
                email="builder@example.com",
                normalized_email="builder@example.com",
                name="Builder",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add_all(
            (
                RoleBindingRecord(
                    id="rb_org1234567890abcd",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=BUILDER_ID,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="member",
                    created_by_user_id=BUILDER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_ws1234567890abcde",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=BUILDER_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="builder",
                    created_by_user_id=BUILDER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )
    await engine.dispose()


@pytest.fixture
async def api_client(tmp_path: Path, service_database: PostgreSQLConfig) -> AsyncIterator[httpx2.AsyncClient]:
    config = settings(tmp_path, service_database)
    await seed_database(config)
    app = create_app(config, components=Components(request_authenticator=authenticate))
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def stage(client: httpx2.AsyncClient, *, key: str, content: bytes) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/skill-uploads",
        content=content,
        headers={"Content-Type": "application/zip", "Idempotency-Key": key},
    )
    assert response.status_code == 201
    receipt = response.json()
    assert receipt["archive_sha256"] == hashlib.sha256(content).hexdigest()
    return receipt


@pytest.mark.anyio
async def test_skill_http_lifecycle_and_content_contract(api_client: httpx2.AsyncClient) -> None:
    upload = await stage(api_client, key="upload-http", content=archive())
    assert (await api_client.get(f"/api/v1/skill-uploads/{upload['upload_id']}")).json() == upload

    created = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/skills",
        json={
            "name": "Deploy Helper",
            "source": {"kind": "zip_upload", "upload_id": upload["upload_id"]},
        },
        headers={"Idempotency-Key": "create-http"},
    )
    assert created.status_code == 201
    publication = created.json()
    skill = publication["skill"]
    revision = publication["revision"]
    assert revision["imported_from"]["archive_sha256"] == upload["archive_sha256"]
    assert publication["outcome"] == "published"
    assert skill["key"] == "deploy-helper"

    listed = await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/skills")
    assert listed.json() == {"items": [{**skill, "default_version": 1, "source_kind": "zip"}], "next_cursor": None}
    assert (await api_client.get(f"/api/v1/skills/{skill['id']}")).json() == skill
    keyed = await api_client.get(f"/api/v1/workspaces/default/skills/{skill['key']}")
    assert keyed.status_code == 200
    assert keyed.json() == skill
    assert keyed.headers["etag"] == created.headers["etag"]
    assert (await api_client.get(f"/api/v1/workspaces/default/skills/{skill['id']}")).status_code == 404
    for query in ("deploy", "DEPLOY-HELPER"):
        searched = await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/skills", params={"q": query})
        assert searched.json()["items"] == [{**skill, "default_version": 1, "source_kind": "zip"}]
    assert (await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/skills", params={"q": "missing"})).json()[
        "items"
    ] == []
    revisions = await api_client.get(f"/api/v1/skills/{skill['id']}/revisions")
    assert revisions.json() == {"items": [revision], "next_cursor": None}
    references = await api_client.get(f"/api/v1/skills/{skill['id']}/references")
    assert references.json() == {"items": [], "next_cursor": None}
    assert (await api_client.get(f"/api/v1/skill-revisions/{revision['id']}")).json() == revision

    content = await api_client.get(f"/api/v1/skill-revisions/{revision['id']}/content")
    assert content.status_code == 200
    assert content.headers["content-type"] == "application/zip"
    assert content.headers["etag"] == f'W/"sha256:{revision["manifest"]["content_digest"]}"'
    assert content.content.startswith(b"PK")

    same_upload = await stage(api_client, key="upload-http-same", content=archive())
    same = await api_client.post(
        f"/api/v1/skills/{skill['id']}/revisions",
        json={
            "expected_version": 1,
            "source": {"kind": "zip_upload", "upload_id": same_upload["upload_id"]},
        },
        headers={"Idempotency-Key": "revision-http-same"},
    )
    assert same.status_code == 200
    assert same.json()["outcome"] == "already_default"

    patched = await api_client.patch(
        f"/api/v1/skills/{skill['id']}",
        json={"name": "Renamed"},
        headers={"If-Match": created.headers["etag"]},
    )
    assert patched.status_code == 200
    assert patched.json()["version"] == 1
    deleted = await api_client.delete(f"/api/v1/skills/{skill['id']}", headers={"If-Match": patched.headers["etag"]})
    assert deleted.status_code == 204
    assert (await api_client.get(f"/api/v1/skills/{skill['id']}")).status_code == 404
    assert (await api_client.get(f"/api/v1/workspaces/default/skills/{skill['key']}")).status_code == 404
    assert (await api_client.get(f"/api/v1/skill-revisions/{revision['id']}")).status_code == 404
    assert (await api_client.get(f"/api/v1/skill-revisions/{revision['id']}/content")).status_code == 404


@pytest.mark.anyio
async def test_set_default_revision_http_contract(api_client: httpx2.AsyncClient) -> None:
    upload = await stage(api_client, key="default-upload", content=archive())
    created = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/skills",
        json={"source": {"kind": "zip_upload", "upload_id": upload["upload_id"]}},
        headers={"Idempotency-Key": "default-create"},
    )
    assert created.status_code == 201
    skill_id = created.json()["skill"]["id"]
    first_revision_id = created.json()["revision"]["id"]
    second_upload = await stage(api_client, key="default-second-upload", content=archive(body="# Second"))
    second = await api_client.post(
        f"/api/v1/skills/{skill_id}/revisions",
        json={"expected_version": 1, "source": {"kind": "zip_upload", "upload_id": second_upload["upload_id"]}},
        headers={"Idempotency-Key": "default-second"},
    )
    assert second.status_code == 201
    head = await api_client.get(f"/api/v1/skills/{skill_id}")
    assert head.json()["default_revision_id"] == second.json()["revision"]["id"]
    path = f"/api/v1/skills/{skill_id}/revisions/{first_revision_id}/default"

    missing = await api_client.post(path)
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "invalid_request"
    unknown = await api_client.post(
        f"/api/v1/skills/{skill_id}/revisions/skr_0000000000000000/default",
        headers={"If-Match": head.headers["etag"]},
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "skill_not_found"

    restored = await api_client.post(path, headers={"If-Match": head.headers["etag"]})
    assert restored.status_code == 200
    assert restored.json()["default_revision_id"] == first_revision_id
    assert restored.json()["version"] == 2
    assert restored.headers["etag"] != head.headers["etag"]
    current = await api_client.get(f"/api/v1/skills/{skill_id}")
    assert current.json() == restored.json()
    assert current.headers["etag"] == restored.headers["etag"]

    stale = await api_client.post(path, headers={"If-Match": head.headers["etag"]})
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "precondition_failed"
    same = await api_client.post(path, headers={"If-Match": restored.headers["etag"]})
    assert same.status_code == 200
    assert same.json() == restored.json()
    assert same.headers["etag"] == restored.headers["etag"]

    listed = await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/skills")
    assert [(item["version"], item["default_version"]) for item in listed.json()["items"]] == [(2, 1)]


@pytest.mark.anyio
async def test_zip_route_rejects_wrong_media_missing_key_and_unknown_fields(
    api_client: httpx2.AsyncClient,
) -> None:
    wrong_media = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/skill-uploads",
        content=archive(),
        headers={"Content-Type": "multipart/form-data", "Idempotency-Key": "wrong-media"},
    )
    assert wrong_media.status_code == 400
    assert wrong_media.json()["error"]["code"] == "invalid_request"

    missing_key = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/skill-uploads",
        content=archive(),
        headers={"Content-Type": "application/zip"},
    )
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "invalid_request"

    upload = await stage(api_client, key="unknown-upload", content=archive())
    unknown = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/skills",
        json={
            "name": "Deploy",
            "source": {"kind": "zip_upload", "upload_id": upload["upload_id"]},
            "object_key": "must-not-be-accepted",
        },
        headers={"Idempotency-Key": "unknown-create"},
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "invalid_request"
    assert "must-not-be-accepted" not in unknown.text


@pytest.mark.anyio
async def test_revision_content_is_emitted_in_bounded_chunks() -> None:
    content = b"x" * (2 * 1024 * 1024 + 1)

    chunks = [chunk async for chunk in _content_chunks(content)]

    assert tuple(map(len, chunks)) == (1024 * 1024, 1024 * 1024, 1)
    assert b"".join(chunks) == content


@pytest.mark.anyio
async def test_skill_labels_http_contract(api_client):
    from tests.labels_support import assert_labels_http_contract

    upload = await stage(api_client, key="label-upload", content=archive())
    collection = f"/api/v1/workspaces/{WORKSPACE_ID}/skills"
    created = await api_client.post(
        collection,
        headers={"Idempotency-Key": "label-skill"},
        json={
            "name": "Label skill",
            "source": {"kind": "zip_upload", "upload_id": upload["upload_id"]},
            "labels": {"initial": "yes"},
        },
    )
    assert created.status_code == 201, created.text
    skill = created.json()["skill"]
    assert skill["labels"] == {"initial": "yes"}
    await assert_labels_http_contract(
        api_client, f"/api/v1/skills/{skill['id']}", collection, immutable_fields=["version", "default_revision_id"]
    )
